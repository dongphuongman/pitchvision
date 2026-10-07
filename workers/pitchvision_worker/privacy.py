"""SCR-40 Data & privacy, worker side: GDPR data exports (D02) and org / account erasure (D03 / D04, AD §14).

``cpu.build_data_export {export_id, org_id}`` (published by the API before its commit → short row waits):
``queued → running`` (guarded), then a zip with ``README.txt``, ``account.json`` (identity-svc
``GET /internal/v1/users/{id}/export``), ``notifications.json`` and the org's tables as JSON — every row of the org
for scope ``org`` (admin+), only rows the user created for scope ``account`` — plus ``positions/{video}.csv`` (scope
``org``). Video files are not included (they are downloaded from the library). Uploaded to
``keys.data_export_key``; ``done`` + ``data_export_ready`` notification (in-app + email) in one transaction; kept
``TTL_DAYS``.

``cpu.privacy_sweep`` (beat, ``PV_PRIVACY_SWEEP_S``, default 10 min):
1. deletes files of expired data exports (``pv_data_export_purge_candidates``);
2. pulls identity-svc's erasure requests for ``PV_REGION`` and purges each subject: org → ``pv_purge_org`` (every
   row, positions included) + every object under ``keys.org_prefixes``; user → ``pv_purge_user`` (notifications,
   data exports, ``created_by`` cleared) + those archives' files. A FINAL pass runs ≥ ``FINAL_PASS_AFTER_S`` later
   (stage tasks / uploads in flight and access tokens issued before the deletion outlive the first pass), then the
   request is acked (``POST /internal/v1/erasures/{id}/done``). Progress lives in ``erasures`` (migration 0008).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import tempfile
import uuid
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from celery import Task
from pitchvision_schemas import keys
from sqlalchemy import Column, MetaData, Table, Text, select, text, update
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.dialects.postgresql import insert as pg_insert

from pitchvision_worker import notify, repository
from pitchvision_worker.celery_app import QUEUE_CPU, TASK_PRIVACY_SWEEP, app
from pitchvision_worker.config import WorkerConfig
from pitchvision_worker.erasure import erase_prefixes
from pitchvision_worker.repository import org_tx
from pitchvision_worker.storage import ObjectStore

log = logging.getLogger(__name__)

TASK_BUILD_DATA_EXPORT = "cpu.build_data_export"
ROW_WAIT_S, ROW_WAIT_TRIES = 2, 5
TTL_DAYS = 7
FINAL_PASS_AFTER_S = 4 * 3600  # like video erasure: > longest stage limit, ≫ access token TTL (15 min)
PURGE_BATCH = 200
HTTP_TIMEOUT_S = 15.0

TZ = TIMESTAMP(timezone=True)
_md = MetaData()
data_exports = Table(  # 0008
    "data_exports", _md,
    Column("id", UUID(as_uuid=True), primary_key=True), Column("org_id", UUID(as_uuid=True), nullable=False),
    Column("user_id", UUID(as_uuid=True), nullable=False), Column("scope", Text, nullable=False),
    Column("status", Text, nullable=False), Column("object_key", Text), Column("size_bytes"), Column("error", JSONB),
    Column("created_at", TZ), Column("started_at", TZ), Column("finished_at", TZ), Column("expires_at", TZ),
)  # fmt: skip
erasures = Table(  # 0008, no RLS
    "erasures", _md,
    Column("request_id", UUID(as_uuid=True), primary_key=True), Column("kind", Text, nullable=False),
    Column("subject_id", UUID(as_uuid=True), nullable=False), Column("passes"), Column("last_pass_at", TZ),
    Column("acked_at", TZ), Column("created_at", TZ),
)  # fmt: skip

# org tables in the archive → the column naming the user who created a row (account scope keeps only those rows)
EXPORT_TABLES: dict[str, str | None] = {
    "videos": "created_by", "jobs": "created_by", "teams": None, "players": None, "corrections": "created_by",
    "shots": None, "exports": "created_by", "stats_player": None, "stats_team": None, "tactics": None,
    "reports": None,
}  # fmt: skip
POSITION_COLUMNS = ("ts_ms", "track_id", "team", "px", "py", "vx", "vy", "q")
README = """PitchVision data export
=======================
account.json         your account at PitchVision (profile, organizations, sign-in methods, active sessions)
notifications.json   your notifications in this organization
{org_part}
Video files are not part of the archive: download them from the library while the organization exists.
Times are UTC (ISO 8601); positions are metres on a 105 x 68 m pitch, sampled at 5 Hz.
"""


def _now() -> datetime:
    return datetime.now(UTC)


def _json_default(v: Any) -> Any:
    if isinstance(v, uuid.UUID | Decimal):
        return str(v)
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, bytes | memoryview):
        return None
    raise TypeError(type(v).__name__)


def _dumps(obj: Any) -> str:
    return json.dumps(obj, default=_json_default, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- data export
Fetch = Callable[[str, dict[str, str]], httpx.Response]


def _get(url: str, headers: dict[str, str]) -> httpx.Response:
    return httpx.get(url, headers=headers, timeout=HTTP_TIMEOUT_S)


@dataclass
class PrivacyDeps:
    cfg: WorkerConfig
    engine: Any
    store: ObjectStore
    fetch: Fetch = _get
    post: notify.Post = notify._post
    tmp_root: str | None = None

    @classmethod
    def from_env(cls) -> PrivacyDeps:
        cfg = WorkerConfig.from_env()
        return cls(cfg, repository.engine_for(cfg.database_url), ObjectStore.from_config(cfg))


def _guarded(engine: Any, org: uuid.UUID, eid: uuid.UUID, status: str, values: dict[str, Any]) -> Any:
    with org_tx(engine, org) as conn:
        return conn.execute(update(data_exports)
                            .where(data_exports.c.id == eid, data_exports.c.status == status).values(**values)
                            .returning(data_exports.c.user_id)).first()  # fmt: skip


def _account(deps: PrivacyDeps, user_id: uuid.UUID) -> dict[str, Any]:
    cfg = deps.cfg
    if not cfg.identity_internal_url or not cfg.internal_token:
        return {"note": "account data is not available from this deployment"}
    r = deps.fetch(f"{cfg.identity_internal_url}/internal/v1/users/{user_id}/export",
                   {"Authorization": f"Bearer {cfg.internal_token}"})  # fmt: skip
    r.raise_for_status()
    out: dict[str, Any] = r.json()
    return out


def _write_archive(deps: PrivacyDeps, conn: Any, row: Mapping[str, Any], path: Path) -> None:
    org, user, scope = row["org_id"], row["user_id"], row["scope"]
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("account.json", _dumps(_account(deps, user)))
        notes = conn.execute(text("SELECT id, type, video_id, data, created_at, read_at FROM notifications "
                                  "WHERE org_id = :o AND user_id = :u ORDER BY created_at"),
                             {"o": org, "u": user}).mappings().all()  # fmt: skip
        z.writestr("notifications.json", _dumps([dict(n) for n in notes]))
        for table, owner in EXPORT_TABLES.items():
            sql = f"SELECT * FROM {table} WHERE org_id = :o"
            params: dict[str, Any] = {"o": org}
            if scope == "account":
                if owner is None:
                    continue
                sql, params = sql + f" AND {owner} = :u", {**params, "u": user}
            rows = conn.execute(text(sql), params).mappings().all()
            z.writestr(f"org/{table}.json", _dumps([dict(r) for r in rows]))
        if scope == "org":
            vids = conn.execute(text("SELECT id FROM videos WHERE org_id = :o AND status <> 'deleted'"),
                                {"o": org}).scalars().all()  # fmt: skip
            for vid in vids:
                buf = io.StringIO()
                w = csv.writer(buf)
                w.writerow(POSITION_COLUMNS)
                res = conn.execute(text(f"SELECT {', '.join(POSITION_COLUMNS)} FROM positions_org "
                                        "WHERE video_id = :v ORDER BY ts_ms, track_id"), {"v": vid})  # fmt: skip
                for r in res:
                    w.writerow(r)
                z.writestr(f"org/positions/{vid}.csv", buf.getvalue())
        org_part = ("org/*.json           the organization's videos, analyses, teams, players, corrections, exports\n"
                    "org/positions/*.csv  tracked positions per video\n") if scope == "org" else (
                    "org/*.json           rows you created in this organization (videos, analyses, corrections,\n"
                    "                     exports); ask an admin for the whole organization's data\n")  # fmt: skip
        z.writestr("README.txt", README.format(org_part=org_part))


def run_build_data_export(org_id: str, export_id: str, deps: PrivacyDeps, *, row_waits: int = 0) -> str:
    """→ done | failed | skipped | wait_row"""
    org, eid = uuid.UUID(org_id), uuid.UUID(export_id)
    with org_tx(deps.engine, org) as conn:
        found = conn.execute(select(data_exports).where(data_exports.c.id == eid)).mappings().first()
    if found is None:
        return "wait_row" if row_waits < ROW_WAIT_TRIES else "skipped"
    row = dict(found)
    if row["status"] != "queued" or _guarded(deps.engine, org, eid, "queued",
                                             {"status": "running", "started_at": _now()}) is None:  # fmt: skip
        return "skipped"
    try:
        with tempfile.TemporaryDirectory(prefix="pv-data-", dir=deps.tmp_root) as tmp:
            path = Path(tmp) / "export.zip"
            with org_tx(deps.engine, org) as conn:  # one snapshot of the org
                _write_archive(deps, conn, row, path)
            key = keys.data_export_key(org_id, export_id)
            deps.store.client.upload_file(str(path), deps.store.bucket, key,
                                          ExtraArgs={"ContentType": "application/zip"})  # fmt: skip
            size = path.stat().st_size
        now = _now()
        with org_tx(deps.engine, org) as conn:
            done = conn.execute(update(data_exports)
                                .where(data_exports.c.id == eid, data_exports.c.status == "running")
                                .values(status="done", object_key=key, size_bytes=size, finished_at=now,
                                        expires_at=now + timedelta(days=TTL_DAYS))
                                .returning(data_exports.c.user_id)).first()  # fmt: skip
            if done is not None:
                notify.add(conn, org_id=org, user_id=done.user_id, type_="data_export_ready", video_id=None,
                           data={"export_id": export_id, "size_bytes": size},
                           dedupe_key=f"data_export:{export_id}")  # fmt: skip
        log.info("data export %s (%s): %d bytes", eid, row["scope"], size)
        return "done"
    except Exception as e:
        log.exception("data export %s failed", eid)
        _guarded(deps.engine, org, eid, "running", {"status": "failed", "finished_at": _now(),
                                                    "error": {"code": "INTERNAL", "detail": repr(e)[:500]}})
        return "failed"


@app.task(name=TASK_BUILD_DATA_EXPORT, queue=QUEUE_CPU, bind=True, acks_late=False, max_retries=ROW_WAIT_TRIES + 1)
def build_data_export(self: Task, export_id: str, org_id: str, row_waits: int = 0) -> str:
    outcome = run_build_data_export(org_id, export_id, PrivacyDeps.from_env(), row_waits=row_waits)
    if outcome == "wait_row":
        raise self.retry(kwargs={"export_id": export_id, "org_id": org_id, "row_waits": row_waits + 1},
                         countdown=ROW_WAIT_S)  # fmt: skip
    return outcome


# ---------------------------------------------------------------- erasure + expired archives
@dataclass(frozen=True)
class SweepResult:
    archives_purged: int = 0
    passes: int = 0
    acked: int = 0


def purge_expired_archives(deps: PrivacyDeps, batch: int = PURGE_BATCH) -> int:
    with deps.engine.begin() as conn:
        doomed = [(r[0], r[1], r[2]) for r in conn.execute(
            text("SELECT export_id, org_id, object_key FROM pv_data_export_purge_candidates(:n)"), {"n": batch})]
    n = 0
    for eid, org, key in doomed:
        deps.store.client.delete_object(Bucket=deps.store.bucket, Key=key)
        with org_tx(deps.engine, org) as conn:
            n += conn.execute(update(data_exports).where(data_exports.c.id == eid, data_exports.c.object_key == key)
                              .values(object_key=None)).rowcount  # fmt: skip
    return n


def erase_subject(deps: PrivacyDeps, kind: str, subject: uuid.UUID) -> int:
    """One purge pass (idempotent) → rows / objects removed."""
    store = deps.store
    if kind == "org":
        with deps.engine.begin() as conn:
            rows = int(conn.execute(text("SELECT pv_purge_org(:o)"), {"o": subject}).scalar() or 0)
        erased = erase_prefixes(store.client, store.bucket, keys.org_prefixes(str(subject)))
        return rows + erased.objects
    with deps.engine.begin() as conn:
        archive_keys = list(conn.execute(text("SELECT pv_purge_user(:u)"), {"u": subject}).scalars())
    for key in archive_keys:
        store.client.delete_object(Bucket=store.bucket, Key=key)
    return len(archive_keys)


def process_erasures(deps: PrivacyDeps, *, final_after_s: float = FINAL_PASS_AFTER_S) -> tuple[int, int]:
    """→ (passes run, requests acked). Without identity config (self-host without identity link) nothing runs."""
    cfg = deps.cfg
    if not cfg.identity_internal_url or not cfg.internal_token:
        return 0, 0
    auth = {"Authorization": f"Bearer {cfg.internal_token}"}
    r = deps.fetch(f"{cfg.identity_internal_url}/internal/v1/erasures?region={cfg.region}&limit=50", auth)
    r.raise_for_status()
    passes = acked = 0
    for req in r.json():
        rid, kind, subject = uuid.UUID(req["id"]), req["kind"], uuid.UUID(req["subject_id"])
        with deps.engine.begin() as conn:
            conn.execute(pg_insert(erasures).values(request_id=rid, kind=kind, subject_id=subject, passes=0)
                         .on_conflict_do_nothing())  # fmt: skip
            state = conn.execute(select(erasures).where(erasures.c.request_id == rid)).mappings().one()
        if state["passes"] < 2:  # still listed after an ack → identity missed it: ack again
            due = state["last_pass_at"] is None or (_now() - state["last_pass_at"]).total_seconds() >= final_after_s
            if not due:
                continue
            n = erase_subject(deps, kind, subject)
            passes += 1
            log.info("erasure %s (%s %s): pass %d removed %d row(s)/object(s)", rid, kind, subject,
                     state["passes"] + 1, n)  # fmt: skip
            with deps.engine.begin() as conn:
                conn.execute(update(erasures).where(erasures.c.request_id == rid)
                             .values(passes=erasures.c.passes + 1, last_pass_at=_now()))  # fmt: skip
            if state["passes"] == 0:
                continue  # the final pass comes on a later sweep
        resp = deps.post(f"{cfg.identity_internal_url}/internal/v1/erasures/{rid}/done", {"region": cfg.region}, auth)
        if resp.status_code == 200:
            acked += 1
            with deps.engine.begin() as conn:
                conn.execute(update(erasures).where(erasures.c.request_id == rid).values(acked_at=_now()))
        else:
            log.warning("erasure %s: ack refused (%d): %s", rid, resp.status_code, resp.text[:200])
    return passes, acked


def run_privacy_sweep(deps: PrivacyDeps) -> SweepResult:
    archives = purge_expired_archives(deps)
    passes, acked = process_erasures(deps)
    if archives or passes or acked:
        log.info("privacy sweep: %d archive(s) purged, %d erasure pass(es), %d acked", archives, passes, acked)
    return SweepResult(archives, passes, acked)


@app.task(name=TASK_PRIVACY_SWEEP, queue=QUEUE_CPU, acks_late=False)
def privacy_sweep() -> dict[str, int]:
    r = run_privacy_sweep(PrivacyDeps.from_env())
    return {"archives_purged": r.archives_purged, "passes": r.passes, "acked": r.acked}


