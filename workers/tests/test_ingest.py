"""S1 YouTube ingest: URL validation, policy, yt-dlp decisions (mocked), ffprobe, task flow, wiring, metrics."""

from __future__ import annotations

import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pitchvision_worker import metrics, repository, tasks
from pitchvision_worker.celery_app import create_app
from pitchvision_worker.config import WorkerConfig, sync_url
from pitchvision_worker.stages import ingest
from pitchvision_worker.stages.ingest import IngestError, MediaInfo

VID = "dQw4w9WgXcQ"
CANON = f"https://www.youtube.com/watch?v={VID}"
INFO: dict[str, Any] = {
    "id": VID,
    "title": "Cup final",
    "duration": 5400,
    "is_live": False,
    "live_status": "not_live",
    "availability": "public",
    "age_limit": 0,
    "thumbnail": "https://i.ytimg.com/vi/x/maxresdefault.jpg",
    "requested_formats": [
        {"format_id": "137", "vcodec": "avc1.640028", "width": 1920, "height": 1080, "fps": 25},
        {"format_id": "140", "vcodec": "none", "acodec": "mp4a.40.2"},
    ],
}


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tasks, "_redis", lambda: None)
    metrics.reset()


# ---------------------------------------------------------------- URL
@pytest.mark.parametrize(
    "url",
    [CANON, f"youtu.be/{VID}", f"https://m.youtube.com/watch?v={VID}&t=3", f"https://youtube.com/shorts/{VID}"],
)
def test_canonical_url(url: str) -> None:
    assert ingest.canonical_youtube_url(url) == CANON


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/video.mp4",
        f"https://www.youtube.com@169.254.169.254/watch?v={VID}",
        f"https://www.youtube.com:9000/watch?v={VID}",
        "https://www.youtube.com/playlist?list=PL1",
        f"https://youtube.com.evil.net/watch?v={VID}",
        f"gopher://www.youtube.com/watch?v={VID}",
    ],
)
def test_bad_url_rejected_before_ytdlp(url: str) -> None:
    fake = FakeYdl()
    with pytest.raises(IngestError) as exc:
        ingest.probe(url, fake.factory)
    assert exc.value.reason == "invalid_url" and not exc.value.retryable
    assert fake.opts == []  # yt-dlp never constructed


# ---------------------------------------------------------------- yt-dlp fakes
@dataclass
class FakeYdl:
    info: dict[str, Any] | None = field(default_factory=lambda: dict(INFO))
    error: Exception | None = None
    download_error: Exception | None = None
    opts: list[dict[str, Any]] = field(default_factory=list)
    extracted: list[tuple[str, bool]] = field(default_factory=list)
    downloads: int = 0

    def factory(self, opts: dict[str, Any]) -> FakeYdl:
        self.opts.append(opts)
        return self

    def __enter__(self) -> FakeYdl:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def extract_info(self, url: str, download: bool = True) -> dict[str, Any] | None:
        self.extracted.append((url, download))
        if self.error:
            raise self.error
        return self.info

    def process_ie_result(self, ie_result: dict[str, Any], download: bool = True) -> dict[str, Any]:
        self.downloads += 1
        if self.download_error:
            raise self.download_error
        tmpl = self.opts[-1]["outtmpl"]["default"]
        path = Path(tmpl.replace("%(ext)s", "mp4"))
        path.write_bytes(b"\x00" * 1024)
        return ie_result | {"requested_downloads": [{"filepath": str(path)}]}


class DownloadError(Exception):
    """Stand-in for yt_dlp.utils.DownloadError (same message format)."""


def test_ydl_options_are_safe() -> None:
    opts = ingest.ydl_options(Path("/tmp/x"))
    assert opts["allowed_extractors"] == ["youtube"]
    assert opts["noplaylist"] is True and opts["merge_output_format"] == "mp4"
    assert not any("cookie" in k for k in opts)
    for alt in opts["format"].split("/"):
        assert "[height<=1080]" in alt
    assert opts["format"].startswith("bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]")


def test_probe_extracts_metadata_without_download() -> None:
    fake = FakeYdl()
    p = ingest.probe(f"https://youtu.be/{VID}", fake.factory)
    assert fake.extracted == [(CANON, False)] and fake.downloads == 0
    assert (p.title, p.duration_s, p.width, p.height, p.fps, p.is_live) == (
        "Cup final",
        5400.0,
        1920,
        1080,
        25.0,
        False,
    )
    assert p.availability == "public" and p.thumbnail_url == INFO["thumbnail"]


@pytest.mark.parametrize(
    ("message", "reason", "retryable"),
    [
        ("ERROR: [youtube] x: Private video. Sign in if you've been granted access to this video", "private", False),
        ("ERROR: [youtube] x: Sign in to confirm your age. This video may be inappropriate for some users.",
         "age_restricted", False),
        ("ERROR: [youtube] x: Sign in to confirm you’re not a bot. Use --cookies-from-browser", "blocked", True),
        ("ERROR: [youtube] x: Join this channel to get access to members-only content", "members_only", False),
        ("ERROR: [youtube] x: This live event will begin in 3 hours.", "live", False),
        ("ERROR: [youtube] x: Video unavailable. This video has been removed by the uploader", "unavailable", False),
        ("ERROR: [youtube] x: Requested format is not available", "no_format", True),
        ("ERROR: unable to download video data: HTTP Error 429: Too Many Requests", "blocked", True),
        ("ERROR: something new and weird", "download_failed", True),
    ],
)  # fmt: skip
def test_classify_yt_dlp_errors(message: str, reason: str, retryable: bool) -> None:
    fake = FakeYdl(error=DownloadError(message))
    with pytest.raises(IngestError) as exc:
        ingest.probe(CANON, fake.factory)
    assert (exc.value.reason, exc.value.retryable) == (reason, retryable)


@pytest.mark.parametrize(
    ("patch", "reason"),
    [
        ({"duration": 3 * 3600 + 1}, "too_long"),
        ({"duration": None}, "unknown_duration"),
        ({"is_live": True, "live_status": "is_live"}, "live"),
        ({"live_status": "post_live"}, "live"),
        ({"availability": "private"}, "private"),
        ({"availability": "premium_only"}, "members_only"),
        ({"age_limit": 18}, "age_restricted"),
    ],
)
def test_policy_blocks_download(tmp_path: Path, patch: dict[str, Any], reason: str) -> None:
    fake = FakeYdl(info=INFO | patch)
    with pytest.raises(IngestError) as exc:
        ingest.download(CANON, tmp_path, fake.factory)
    assert exc.value.reason == reason
    assert fake.downloads == 0


def test_download_writes_into_workdir(tmp_path: Path) -> None:
    fake = FakeYdl()
    got = ingest.download(f"https://www.youtube.com/live/{VID}", tmp_path, fake.factory)
    assert got.path == tmp_path / "source.mp4" and got.path.stat().st_size == 1024
    assert got.probe.height == 1080 and fake.downloads == 1
    assert fake.opts[-1]["outtmpl"]["default"].startswith(str(tmp_path))


# ---------------------------------------------------------------- ffprobe
def test_parse_ffprobe() -> None:
    data = {
        "streams": [
            {"codec_type": "audio", "codec_name": "aac"},
            {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720,
             "avg_frame_rate": "30000/1001", "r_frame_rate": "30000/1001"},
        ],
        "format": {"duration": "12.345"},
    }  # fmt: skip
    assert ingest.parse_ffprobe(data) == MediaInfo(12345, 1280, 720, 29.97, "h264")
    with pytest.raises(IngestError, match="no video stream"):
        ingest.parse_ffprobe({"streams": [{"codec_type": "audio"}]})


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_ffprobe_real_file(tmp_path: Path) -> None:
    out = tmp_path / "t.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25:duration=1",
         "-pix_fmt", "yuv420p", "-c:v", "libx264", str(out)],
        check=True, timeout=60,
    )  # fmt: skip
    m = ingest.ffprobe(out)
    assert (m.width, m.height, m.fps, m.vcodec) == (320, 180, 25.0, "h264") and m.duration_ms == 1000
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(IngestError) as exc:
        ingest.ffprobe(bad)
    assert exc.value.reason == "unreadable"


# ---------------------------------------------------------------- task flow (fake DB + storage)
@dataclass
class FakeStore:
    status: str | None = "ingesting"
    ready: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)
    accept_update: bool = True


class FakeS3:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete_object(self, Bucket: str, Key: str) -> None:  # boto3 keyword names
        self.deleted.append(Key)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    st = FakeStore()
    monkeypatch.setattr(repository, "get_status", lambda engine, org, vid: st.status)

    def mark_ready(engine: Any, org: Any, vid: Any, **kw: Any) -> bool:
        st.ready.append(kw)
        return st.accept_update

    def mark_failed(engine: Any, org: Any, vid: Any, error: dict[str, Any]) -> bool:
        st.failed.append(error)
        return True

    monkeypatch.setattr(repository, "mark_ready", mark_ready)
    monkeypatch.setattr(repository, "mark_failed", mark_failed)
    monkeypatch.setattr(tasks, "upload_file", lambda client, bucket, key, path: path.stat().st_size)
    return st


def deps(fake: FakeYdl, tmp_path: Path, s3: FakeS3 | None = None) -> tasks.IngestDeps:
    cfg = WorkerConfig.from_env({"PV_STORAGE_BUCKET": "b"})
    media = MediaInfo(5_400_000, 1920, 1080, 25.0, "h264")
    return tasks.IngestDeps(cfg, None, s3 or FakeS3(), None, fake.factory, lambda p: media, str(tmp_path))


def run(d: tasks.IngestDeps, **kw: int) -> tuple[str, uuid.UUID, uuid.UUID, tasks.Outcome]:
    vid, org = uuid.uuid4(), uuid.uuid4()
    return CANON, vid, org, tasks.run_download(str(vid), str(org), CANON, d, **kw)


def test_download_success_marks_ready(store: FakeStore, tmp_path: Path) -> None:
    _, vid, org, outcome = run(deps(FakeYdl(), tmp_path))
    assert outcome == tasks.Outcome("done")
    [ready] = store.ready
    assert ready["object_key"] == f"orgs/{org}/videos/{vid}/source/original.mp4"
    assert ready | {"object_key": None} == {
        "object_key": None, "size_bytes": 1024, "duration_ms": 5_400_000, "fps": 25.0, "width": 1920,
        "height": 1080, "title": "Cup final", "thumbnail_url": INFO["thumbnail"],
    }  # fmt: skip
    assert list(tmp_path.iterdir()) == []  # temp dir cleaned up
    assert metrics.snapshot() == {("download", "success", ""): 1}


def test_transient_failures_retry_with_backoff_then_fail(store: FakeStore, tmp_path: Path) -> None:
    fake = FakeYdl(error=DownloadError("Sign in to confirm you're not a bot"))
    outcomes = [run(deps(fake, tmp_path), failures=n)[3] for n in range(4)]
    assert [(o.kind, o.retry_in) for o in outcomes] == [("retry", 30), ("retry", 120), ("retry", 300), ("failed", None)]
    assert store.failed == [{"code": "INGEST_FAILED", "reason": "blocked", "detail": fake.error.args[0]}]  # type: ignore[union-attr]
    assert metrics.snapshot() == {("download", "retry", "blocked"): 3, ("download", "failed", "blocked"): 1}


def test_permanent_failure_is_not_retried(store: FakeStore, tmp_path: Path) -> None:
    outcome = run(deps(FakeYdl(info=INFO | {"duration": 4 * 3600}), tmp_path))[3]
    assert outcome.kind == "failed" and outcome.reason == "too_long"
    assert store.failed[0]["reason"] == "too_long" and store.ready == []
    assert metrics.snapshot() == {("download", "rejected", "too_long"): 1}


def test_row_not_visible_yet_then_skip(store: FakeStore, tmp_path: Path) -> None:
    store.status = None
    assert run(deps(FakeYdl(), tmp_path))[3] == tasks.Outcome("wait_row", 2)
    assert run(deps(FakeYdl(), tmp_path), row_waits=5)[3].kind == "skipped"
    store.status = "ready"
    fake = FakeYdl()
    assert run(deps(fake, tmp_path))[3] == tasks.Outcome("skipped", reason="ready")
    assert fake.opts == []


def test_deleted_during_download_removes_object(store: FakeStore, tmp_path: Path) -> None:
    store.accept_update = False
    s3 = FakeS3()
    _, vid, org, outcome = run(deps(FakeYdl(), tmp_path, s3))
    assert outcome.kind == "skipped" and s3.deleted == [f"orgs/{org}/videos/{vid}/source/original.mp4"]


def test_storage_error_is_retryable(store: FakeStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any) -> int:
        raise ConnectionError("r2 down")

    monkeypatch.setattr(tasks, "upload_file", boom)
    outcome = run(deps(FakeYdl(), tmp_path))[3]
    assert outcome.kind == "retry" and outcome.reason == "storage_failed"


def test_run_probe_shapes(monkeypatch: pytest.MonkeyPatch) -> None:
    ok = tasks.run_probe(CANON, FakeYdl().factory)
    assert ok["ok"] is True and ok["info"]["duration_s"] == 5400.0 and ok["info"]["height"] == 1080
    bad = tasks.run_probe(CANON, FakeYdl(error=DownloadError("Private video")).factory)
    assert bad == {"ok": False, "reason": "private", "detail": "Private video"}
    assert metrics.snapshot() == {("probe", "success", ""): 1, ("probe", "rejected", "private"): 1}


# ---------------------------------------------------------------- wiring
def test_tasks_registered_and_routed_to_ingest_queue() -> None:
    app = create_app("memory://")
    app.loader.import_default_modules()
    assert {"ingest.probe_url", "ingest.download", "pitchvision_worker.ping"} <= set(app.tasks)
    for name in ("ingest.probe_url", "ingest.download"):
        assert app.amqp.router.route({}, name)["queue"].name == "ingest"
    assert app.conf.broker_transport_options["visibility_timeout"] >= 5 * 3600 + 600


def test_download_task_retries_via_celery(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[int, int]] = []
    script = [tasks.Outcome("wait_row", 0), tasks.Outcome("retry", 0), tasks.Outcome("done")]

    def fake_run(video_id: str, org_id: str, url: str, d: Any, *, failures: int, row_waits: int) -> tasks.Outcome:
        seen.append((failures, row_waits))
        return script[len(seen) - 1]

    monkeypatch.setattr(tasks, "run_download", fake_run)
    monkeypatch.setattr(tasks.IngestDeps, "from_env", classmethod(lambda cls: None))
    result = tasks.download.apply(kwargs={"video_id": str(uuid.uuid4()), "org_id": str(uuid.uuid4()), "url": CANON})
    assert result.get() == "done"
    assert seen == [(0, 0), (0, 1), (1, 1)]


def test_config_sync_url() -> None:
    assert sync_url("postgresql+asyncpg://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    cfg = WorkerConfig.from_env({"PV_DATABASE_URL": "postgresql+asyncpg://a:b@pg/x", "PV_STORAGE_REGION": "us-east-1"})
    assert cfg.database_url == "postgresql+psycopg://a:b@pg/x" and cfg.storage_region == "us-east-1"
    assert WorkerConfig.from_env({"PV_WORKER_DATABASE_URL": "postgresql://w@pg/x"}).database_url.startswith(
        "postgresql+psycopg://w@"
    )
