"""S8 load-db writer (M1-BE-14): a ``LoadBatch`` → ``positions`` (COPY) + ``teams`` + ``players``, one transaction.

Runs as ``pv_worker`` inside ``org_tx`` (app.org_id set): teams / players go through RLS like every tenant table;
``positions`` (no RLS, compressed hypertable) is protected by the ``positions_org_guard`` trigger on insert and is
only deleted through ``pv_delete_positions(video)``, which is scoped to app.org_id (migration 0004).

Idempotent per (video, revision): a re-run deletes the video's positions and re-COPYs them, upserts teams by
``cluster`` and players by ``(video_id, track_id)`` and drops players whose track no longer exists. Rows the user
confirmed (``confirmed = true``: names, colours, team assignment) are never overwritten or deleted. ``positions`` has
no revision column (AD §9.3): the latest load of a video is its positions.
"""

from __future__ import annotations

import math
import uuid
from typing import Any

from sqlalchemy import Engine, delete, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from pitchvision_worker.repository import org_tx, players, stats_player, teams
from pitchvision_worker.stages.load_db import POSITION_COLUMNS, LoadBatch

COPY_SQL = "COPY positions (video_id, org_id, ts_ms, track_id, team, px, py, vx, vy, q) FROM STDIN"
PLAYER_CHUNK = 1000


def _num(v: float) -> float | None:
    return float(v) if math.isfinite(v) else None


def attack_for(cluster0: str | None, cluster: int) -> str | None:
    """Cluster 0's first-half direction → this cluster's (the other team attacks the opposite side)."""
    if cluster0 is None or cluster == 0:
        return cluster0
    return "left" if cluster0 == "right" else "right"


def write_results(engine: Engine, org_id: uuid.UUID, video_id: uuid.UUID, batch: LoadBatch, revision: int = 0
                  ) -> dict[str, int]:  # fmt: skip
    with org_tx(engine, org_id) as conn:
        deleted = int(conn.execute(text("SELECT pv_delete_positions(:v)"), {"v": video_id}).scalar_one())
        p = batch.positions
        cols = [p[c].tolist() for c in POSITION_COLUMNS]
        raw: Any = conn.connection.driver_connection  # psycopg 3 connection of this transaction
        with raw.cursor() as cur, cur.copy(COPY_SQL) as cp:
            for ts, tid, team, px, py, vx, vy, q in zip(*cols, strict=True):
                cp.write_row((video_id, org_id, int(ts), int(tid), int(team) if team in (0, 1) else None,
                              _num(px), _num(py), _num(vx), _num(vy), _num(q)))  # fmt: skip

        existing = {r["cluster"]: dict(r) for r in conn.execute(
            select(teams).where(teams.c.video_id == video_id)).mappings()}  # fmt: skip
        team_ids: dict[int, uuid.UUID] = {}
        user_attack = any(r["attack_source"] == "user" for r in existing.values())
        for t in batch.teams:
            row = existing.get(t.cluster)
            attack = attack_for(batch.attack_h1, t.cluster)
            auto = {} if user_attack else {"attack_h1": attack, "attack_source": "auto" if attack else None}
            if row is None:
                new_team = insert(teams).values(video_id=video_id, org_id=org_id, cluster=t.cluster, name=t.name,
                                                color=t.color, **auto).returning(teams.c.id)  # fmt: skip
                team_ids[t.cluster] = conn.execute(new_team).scalar_one()
                continue
            team_ids[t.cluster] = row["id"]
            values = auto if row["confirmed"] else {"name": t.name, "color": t.color, **auto}
            if values:
                conn.execute(update(teams).where(teams.c.id == row["id"]).values(**values))

        rows = [{"video_id": video_id, "org_id": org_id, "track_id": pl.track_id, "role": pl.role,
                 "team_id": team_ids.get(pl.cluster) if pl.cluster is not None else None, "frames": pl.frames,
                 "t0_ms": pl.t0_ms, "t1_ms": pl.t1_ms} for pl in batch.players]  # fmt: skip
        for s in range(0, len(rows), PLAYER_CHUNK):
            stmt = pg_insert(players).values(rows[s:s + PLAYER_CHUNK])
            conn.execute(stmt.on_conflict_do_update(
                index_elements=["video_id", "track_id"],
                set_={"team_id": stmt.excluded.team_id, "role": stmt.excluded.role, "frames": stmt.excluded.frames,
                      "t0_ms": stmt.excluded.t0_ms, "t1_ms": stmt.excluded.t1_ms},
                where=players.c.confirmed.is_(False)))  # fmt: skip
        stale = delete(players).where(players.c.video_id == video_id, players.c.confirmed.is_(False))
        if rows:
            stale = stale.where(players.c.track_id.not_in([r["track_id"] for r in rows]))
        removed = conn.execute(stale).rowcount

        # S9 physical stats per player row (= per track in M1); the API sums them into teams with the current
        # team / role, so team fixes made later still count. Replaced wholesale: they belong to this revision.
        conn.execute(delete(stats_player).where(stats_player.c.video_id == video_id))
        ids = {r.track_id: r.id for r in conn.execute(
            select(players.c.track_id, players.c.id).where(players.c.video_id == video_id))}  # fmt: skip
        stat_rows = [{"video_id": video_id, "org_id": org_id, "player_id": ids[s.track_id], "revision": revision,
                      "metrics": s.metrics()} for s in batch.track_stats if s.track_id in ids]  # fmt: skip
        for s in range(0, len(stat_rows), PLAYER_CHUNK):
            conn.execute(insert(stats_player).values(stat_rows[s:s + PLAYER_CHUNK]))
    return {"positions": len(cols[0]), "positions_replaced": deleted, "teams": len(team_ids),
            "players": len(rows), "players_removed": int(removed or 0), "stats_players": len(stat_rows)}
