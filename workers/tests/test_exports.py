"""S11 export render (M1-BE-17): drawing + ffmpeg pipeline on a synthetic clip, and the cpu.render_export /
cpu.sweep_exports tasks against real Postgres (pv_worker, RLS) + S3."""
from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from cv_core.io.parquet import CLS_BALL, CLS_GOALKEEPER, CLS_PLAYER, CLS_REFEREE, NO_TRACK, write_tracks
from pitchvision_schemas import ExportLayers, ExportSpec, TeamInfo, TrackOverride, keys
from pitchvision_worker import export_render
from pitchvision_worker.config import WorkerConfig
from pitchvision_worker.export_render import Painter, RenderCancelled, frame_range, render
from pitchvision_worker.exports import ExportDeps, run_render_export, run_sweep
from pitchvision_worker.repository import org_tx
from pitchvision_worker.storage import ObjectStore
from sqlalchemy import Engine, create_engine, text
from synth_pitch_clip import FPS, H, W, write_clip
from worker_testkit import S3_BUCKET, s3_test_client

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

RED, BLUE = "#ff0000", "#0000ff"
USER = uuid.UUID("00000000-0000-4000-8000-0000000000aa")


def tracks_file(path: Path, frames: int = 50) -> Path:
    """Track 1 (team 0) box (100, 100)-(140, 200) moving right 2 px/frame, goalkeeper 2 (team 1), referee 3, ball."""
    cols = ("frame_idx", "ts_ms", "track_id", "cls", "team", "x1", "y1", "x2", "y2", "conf", "px", "py", "h_quality",
            "interpolated")  # fmt: skip
    rows: dict[str, list[Any]] = {k: [] for k in cols}

    def add(f: int, tid: int, cls: int, team: int, box: tuple[int, int, int, int], pxy: tuple[float, float]) -> None:
        for k, v in zip(cols, (f, f * 40, tid, cls, team, *box, 0.87, *pxy, 0.9, False), strict=True):
            rows[k].append(v)

    for f in range(frames):
        add(f, 1, CLS_PLAYER, 0, (100 + 2 * f, 100, 140 + 2 * f, 200), (30.0, 20.0))
        add(f, 2, CLS_GOALKEEPER, 1, (400, 120, 430, 190), (5.0, 34.0))
        add(f, 3, CLS_REFEREE, -1, (250, 150, 270, 210), (float("nan"), float("nan")))
        add(f, NO_TRACK, CLS_BALL, -1, (300, 250, 308, 258), (52.5, 34.0))
    return write_tracks(path, {k: np.asarray(v) for k, v in rows.items()})


def spec(**over: Any) -> ExportSpec:
    base: dict[str, Any] = {"job_id": str(uuid.uuid4()), "revision": 0, "from_ms": 400, "to_ms": 1600, "fps": FPS,
                            "width": W, "height": H, "out_height": 720, "layers": ExportLayers(conf=True, trails=True),
                            "teams": [TeamInfo(cluster=0, name="A", color=RED), TeamInfo(cluster=1, name="B",
                                                                                         color=BLUE)],
                            "pitch_status": "ok"}  # fmt: skip
    return ExportSpec(**(base | over))


def probe(path: Path) -> dict[str, Any]:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, check=True)  # fmt: skip
    data: dict[str, Any] = json.loads(out.stdout)
    return data


def first_frame(path: Path) -> np.ndarray:
    cap = cv2.VideoCapture(str(path))
    ok, img = cap.read()
    cap.release()
    assert ok
    return img


# ---------------------------------------------------------------- render (no services)
def test_frame_range() -> None:
    assert frame_range(spec(from_ms=400, to_ms=1600)) == (10, 40)
    assert frame_range(spec(from_ms=401, to_ms=1601)) == (11, 41)


def test_painter_draws_team_boxes_and_watermark() -> None:
    s = spec(layout="video", out_height=720)
    p = Painter(s, 1280, 720)
    frame = np.zeros((720, 1280, 3), np.uint8)
    rows = {"track_id": np.array([1, 3]), "cls": np.array([CLS_PLAYER, CLS_REFEREE]), "team": np.array([0, -1]),
            "x1": np.array([100, 250]), "y1": np.array([100, 150]), "x2": np.array([140, 270]),
            "y2": np.array([200, 210]), "conf": np.array([0.9, 0.8]), "px": np.array([1.0, np.nan]),
            "py": np.array([1.0, np.nan])}  # fmt: skip
    out = p.paint(frame, 0, rows)
    assert out.shape == (720, 1280, 3)
    assert tuple(out[300, 200]) == (0, 0, 255)  # left edge of track 1's box (×2), BGR red
    assert out[400, 400].sum() == 0  # inside the box: untouched
    assert out[690:715, 1050:1270].max() > 100  # watermark, bottom-right
    assert Painter(spec(watermark=False, layout="video"), 1280, 720).wm is None
    # SCR-14 E06: track 1 fixed to team 1 → drawn blue
    fixed = Painter(spec(layout="video", track_overrides=[TrackOverride(track_id=1, team=1, cls=1)]), 1280, 720)
    out = fixed.paint(np.zeros((720, 1280, 3), np.uint8), 0, rows)
    assert tuple(out[300, 200]) == (255, 0, 0) and rows["team"][0] == 0  # BGR blue; input rows untouched


def test_render_range_layers_map(tmp_path: Path) -> None:
    clip = write_clip(tmp_path / "n.mp4", 2.0)
    out = tmp_path / "out.mp4"
    calls: list[float] = []
    res = render(spec(layout="video_map"), str(clip), tracks_file(tmp_path / "t.parquet"), out,
                 on_progress=lambda pct: calls.append(pct) is None)  # fmt: skip
    assert res.frames == 30 and res.height == 720 and res.width > 1280
    v = next(st for st in probe(out)["streams"] if st["codec_type"] == "video")
    assert (v["codec_name"], v["pix_fmt"], int(v["width"]), int(v["height"])) == ("h264", "yuv420p", res.width, 720)
    assert int(v["nb_frames"]) == 30
    img = first_frame(out)
    # frame 10: track 1 at x1 = 120 → 240 px at 720p (W 640 → 1280); team 0 is red (H.264 shifts colours a bit)
    b, g, r = (int(c) for c in img[300, 240])
    assert r > 150 and b < 100 and g < 100
    panel = img[:, 1290:]
    assert panel[:, :, 1].mean() > panel[:, :, 2].mean()  # green pitch panel on the right


def test_render_cancel_and_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clip = write_clip(tmp_path / "n.mp4", 2.0)
    tracks = tracks_file(tmp_path / "t.parquet")
    monkeypatch.setattr(export_render, "PROGRESS_EVERY_S", 0.0)
    with pytest.raises(RenderCancelled):
        render(spec(), str(clip), tracks, tmp_path / "o.mp4", on_progress=lambda _pct: False)
    with pytest.raises(export_render.RenderError) as ei:
        render(spec(), str(tmp_path / "missing.mp4"), tracks, tmp_path / "o2.mp4")
    assert ei.value.code == "VIDEO_UNREADABLE"


# ---------------------------------------------------------------- tasks (Docker: Postgres + S3)
@pytest.fixture
def worker_db(pg_worker_url: str) -> Iterator[Engine]:
    e = create_engine(pg_worker_url)
    yield e
    e.dispose()


def _rows(engine: Engine, org: uuid.UUID, eid: uuid.UUID) -> dict[str, Any]:
    with org_tx(engine, org) as c:
        return dict(c.execute(text("SELECT * FROM exports WHERE id = :e"), {"e": eid}).mappings().one())


def _export(engine: Engine, org: uuid.UUID, vid: uuid.UUID, sp: ExportSpec, **cols: Any) -> uuid.UUID:
    eid = uuid.uuid4()
    with org_tx(engine, org) as c:
        c.execute(text("INSERT INTO exports (id, org_id, video_id, spec, filename, created_by) "
                       "VALUES (:e, :o, :v, :s, 'x.mp4', :u)"),
                  {"e": eid, "o": org, "v": vid, "s": json.dumps(sp.model_dump(mode="json")), "u": USER})  # fmt: skip
        if cols:
            sets = ", ".join(f"{k} = :{k}" for k in cols)
            c.execute(text(f"UPDATE exports SET {sets} WHERE id = :e"), {"e": eid, **cols})
    return eid


@pytest.fixture
def setup(tmp_path: Path, worker_db: Engine, s3_endpoint: str) -> Iterator[tuple[ExportDeps, uuid.UUID, uuid.UUID]]:
    org, vid = uuid.uuid4(), uuid.uuid4()
    with org_tx(worker_db, org) as c:
        c.execute(text("INSERT INTO videos (id, org_id, created_by, source_type, status) "
                       "VALUES (:v, :o, :u, 'upload', 'ready')"), {"v": vid, "o": org, "u": uuid.uuid4()})  # fmt: skip
    store = ObjectStore(s3_test_client(s3_endpoint), S3_BUCKET)
    store.upload(keys.normalized_key(str(org), str(vid)), write_clip(tmp_path / "n.mp4", 2.0))
    deps = ExportDeps(WorkerConfig.from_env(), worker_db, store, tmp_root=str(tmp_path))
    yield deps, org, vid


def _with_tracks(deps: ExportDeps, org: uuid.UUID, vid: uuid.UUID, tmp: Path) -> ExportSpec:
    sp = spec(layout="video", out_height=720)
    deps.store.upload(keys.job_results_prefix(str(org), str(vid), sp.job_id) + "tracks/tracks.parquet",
                      tracks_file(tmp / "t.parquet"))  # fmt: skip
    return sp


def test_render_export_task(tmp_path: Path, setup: tuple[ExportDeps, uuid.UUID, uuid.UUID]) -> None:
    deps, org, vid = setup
    sp = _with_tracks(deps, org, vid, tmp_path)
    eid = _export(deps.engine, org, vid, sp)
    assert run_render_export(str(org), str(eid), deps) == "done"
    row = _rows(deps.engine, org, eid)
    key = keys.export_key(str(org), str(vid), str(eid), "mp4")
    assert (row["status"], float(row["progress"]), row["object_key"], row["attempts"]) == ("done", 100.0, key, 1)
    assert row["size_bytes"] == len(deps.store.get_bytes(key)) > 0
    assert timedelta(days=6.9) < row["expires_at"] - row["finished_at"] <= timedelta(days=7)
    assert run_render_export(str(org), str(eid), deps) == "skipped"  # redelivery
    with org_tx(deps.engine, org) as c:
        notes = list(c.execute(text("SELECT type, user_id, data->>'export_id', data->>'filename' FROM notifications "
                                    "WHERE video_id = :v"), {"v": vid}))  # fmt: skip
    assert [tuple(n) for n in notes] == [("export_ready", USER, str(eid), "x.mp4")]
    assert run_render_export(str(uuid.uuid4()), str(eid), deps, row_waits=0) == "wait_row"  # other org: invisible
    assert run_render_export(str(uuid.uuid4()), str(eid), deps, row_waits=9) == "skipped"


def test_render_export_cancel_and_failures(tmp_path: Path, setup: tuple[ExportDeps, uuid.UUID, uuid.UUID]) -> None:
    deps, org, vid = setup
    sp = _with_tracks(deps, org, vid, tmp_path)
    eid = _export(deps.engine, org, vid, sp)

    def cancelled_midway(spec_: ExportSpec, url: str, tracks: Path, out: Path, *, on_progress: Any) -> Any:
        with org_tx(deps.engine, org) as c:  # the API cancels while it renders
            c.execute(text("UPDATE exports SET status = 'cancelled' WHERE id = :e"), {"e": eid})
        if not on_progress(50.0):
            raise RenderCancelled
        raise AssertionError("render should have been told to stop")

    deps.render = cancelled_midway
    assert run_render_export(str(org), str(eid), deps) == "cancelled"
    assert _rows(deps.engine, org, eid)["status"] == "cancelled"
    assert not deps.store.exists(keys.export_key(str(org), str(vid), str(eid), "mp4"))

    gone = _export(deps.engine, org, vid, spec())  # its job's tracks were never written
    deps.render = render
    assert run_render_export(str(org), str(gone), deps) == "failed"
    err = _rows(deps.engine, org, gone)["error"]
    assert err["code"] == "EXPORT_FAILED" and err["retryable"] is False


def test_sweep_fails_stale_and_purges(tmp_path: Path, setup: tuple[ExportDeps, uuid.UUID, uuid.UUID]) -> None:
    deps, org, vid = setup
    old = datetime.now(UTC) - timedelta(hours=1)
    stale = _export(deps.engine, org, vid, spec(), status="running", started_at=old, heartbeat_at=old)
    alive = _export(deps.engine, org, vid, spec(), status="running", started_at=old, heartbeat_at=datetime.now(UTC))
    keys_ = [keys.export_key(str(org), str(vid), f"e{i}", "mp4") for i in range(3)]
    for k in keys_:
        deps.store.put_bytes(k, b"x", "video/mp4")
    expired = _export(deps.engine, org, vid, spec(), status="done", object_key=keys_[0], expires_at=old)
    deleted = _export(deps.engine, org, vid, spec(), status="done", object_key=keys_[1],
                      expires_at=datetime.now(UTC) + timedelta(days=3), deleted_at=old)  # fmt: skip
    kept = _export(deps.engine, org, vid, spec(), status="done", object_key=keys_[2],
                   expires_at=datetime.now(UTC) + timedelta(days=3))  # fmt: skip
    r = run_sweep(deps.engine, deps.store)
    assert r.stale >= 1 and r.purged >= 2
    assert _rows(deps.engine, org, stale)["error"]["retryable"] is True
    assert _rows(deps.engine, org, stale)["status"] == "failed"
    assert _rows(deps.engine, org, alive)["status"] == "running"
    assert [_rows(deps.engine, org, e)["object_key"] for e in (expired, deleted, kept)] == [None, None, keys_[2]]
    assert [deps.store.exists(k) for k in keys_] == [False, False, True]
