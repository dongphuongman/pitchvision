"""Stage S1 — YouTube ingest (M1-BE-08): probe metadata, enforce policy, download best ≤ 1080p, ffprobe.

Pure logic with an injectable yt-dlp factory so tests never touch the network. Celery wiring, storage and
DB writes live in pitchvision_worker.tasks.

- yt-dlp is pinned (workers/pyproject.toml); SP-10: older versions get bot-checked. No cookies, ever.
- Only the YouTube extractor is enabled (`allowed_extractors`), and the URL is re-validated and rebuilt
  from its video id here too — the generic extractor would follow arbitrary URLs (SSRF, AD §14).
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

from cv_core.io.limits import PROBE_MAX_MEM_MB, child_limits

log = logging.getLogger(__name__)

MAX_DURATION_S = 3 * 3600
MAX_HEIGHT = 1080

# ---------------------------------------------------------------- URL
# keep in sync with pitchvision_api.services.youtube_url
YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"})
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_PATH_ID_PREFIXES = ("shorts", "live", "embed", "v")


def canonical_youtube_url(raw: str) -> str:
    """https://www.youtube.com/watch?v=<id> or IngestError(invalid_url)."""
    s = raw.strip()
    if not s or len(s) > 2048 or any(c.isspace() or ord(c) < 0x20 or c == "\\" for c in s):
        raise IngestError("invalid_url", "malformed URL")
    if "://" not in s:
        s = "https://" + s
    try:
        u = urlsplit(s)
        port = u.port
    except ValueError:
        raise IngestError("invalid_url", "unparseable URL") from None
    host = (u.hostname or "").lower()
    if (
        u.scheme.lower() not in ("http", "https")
        or u.username is not None
        or u.password is not None
        or port not in (None, 80, 443)
        or host not in YOUTUBE_HOSTS
    ):
        raise IngestError("invalid_url", "not an allowed YouTube URL")
    segments = [p for p in u.path.split("/") if p]
    candidate: str | None = None
    if host == "youtu.be":
        candidate = segments[0] if len(segments) == 1 else None
    elif segments == ["watch"]:
        values = parse_qs(u.query).get("v", [])
        candidate = values[0] if len(values) == 1 else None
    elif len(segments) == 2 and segments[0] in _PATH_ID_PREFIXES:
        candidate = segments[1]
    if candidate is None or not _VIDEO_ID.fullmatch(candidate):
        raise IngestError("invalid_url", "URL does not point at a single video")
    return f"https://www.youtube.com/watch?v={candidate}"


# ---------------------------------------------------------------- errors
# reasons the user must fix (another video) — never retried
PERMANENT_REASONS = frozenset(
    {
        "invalid_url",
        "too_long",
        "unknown_duration",
        "live",
        "private",
        "members_only",
        "needs_auth",
        "age_restricted",
        "unavailable",
        "unreadable",
    }
)


class IngestError(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason, self.detail = reason, detail[:500]

    @property
    def retryable(self) -> bool:
        return self.reason not in PERMANENT_REASONS


# (pattern in yt-dlp's message, reason) — first match wins; messages are YouTube's wording via yt-dlp
_ERROR_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(p, re.IGNORECASE), r)
    for p, r in (
        (r"not a bot|HTTP Error 429|too many requests|rate.?limit", "blocked"),
        (r"confirm your age|age.?restricted|inappropriate for some users", "age_restricted"),
        (r"private video|video is private", "private"),
        (r"members.only|join this channel|premium members|requires payment", "members_only"),
        (r"live event will begin|premieres in|is_upcoming|this live stream recording is not available", "live"),
        (r"sign in", "needs_auth"),
        (r"requested format is not available", "no_format"),
        (r"video unavailable|has been removed|no longer available|not available|account.*terminated", "unavailable"),
        (r"unsupported url|no suitable extractor", "invalid_url"),
    )
)


def classify_error(exc: BaseException) -> IngestError:
    if isinstance(exc, IngestError):
        return exc
    msg = str(exc)
    for pattern, reason in _ERROR_PATTERNS:
        if pattern.search(msg):
            return IngestError(reason, msg)
    return IngestError("download_failed", msg)


# ---------------------------------------------------------------- yt-dlp
class YoutubeDLLike(Protocol):
    def extract_info(self, url: str, download: bool = ...) -> dict[str, Any] | None: ...

    def process_ie_result(self, ie_result: dict[str, Any], download: bool = ...) -> dict[str, Any]: ...

    def __enter__(self) -> YoutubeDLLike: ...

    def __exit__(self, *exc: object) -> None: ...


YdlFactory = Callable[[dict[str, Any]], YoutubeDLLike]

# Best stream ≤ 1080p; prefer H.264 + AAC in MP4 (decodes everywhere, no re-encode), then any mp4, then anything.
FORMAT = (
    f"bv*[height<={MAX_HEIGHT}][vcodec^=avc1]+ba[ext=m4a]"
    f"/bv*[height<={MAX_HEIGHT}][ext=mp4]+ba[ext=m4a]"
    f"/bv*[height<={MAX_HEIGHT}]+ba"
    f"/b[height<={MAX_HEIGHT}]"
)


def ydl_options(workdir: Path | None = None) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "allowed_extractors": ["youtube"],  # exactly the single-video extractor (no generic, no tabs)
        "format": FORMAT,
        "merge_output_format": "mp4",
        "socket_timeout": 20,
        "retries": 5,
        "fragment_retries": 10,
        "extractor_retries": 2,
        "cachedir": False,
        # deliberately no cookiefile / cookiesfrombrowser: we only ingest what is public
    }
    if workdir is not None:
        opts["outtmpl"] = {"default": str(workdir / "source.%(ext)s")}
        opts["paths"] = {"home": str(workdir), "temp": str(workdir)}
    return opts


def default_ydl_factory(opts: dict[str, Any]) -> YoutubeDLLike:
    import yt_dlp

    ydl: YoutubeDLLike = yt_dlp.YoutubeDL(opts)  # type: ignore[arg-type]
    return ydl


@dataclass(frozen=True)
class ProbeInfo:
    title: str | None
    duration_s: float | None
    width: int | None
    height: int | None
    fps: float | None
    is_live: bool
    live_status: str | None
    availability: str | None
    age_limit: int
    thumbnail_url: str | None

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _video_format(info: Mapping[str, Any]) -> Mapping[str, Any]:
    for f in info.get("requested_formats") or ():
        if f.get("vcodec") not in (None, "none"):
            return f  # type: ignore[no-any-return]
    return info


def probe_info(info: Mapping[str, Any]) -> ProbeInfo:
    vf = _video_format(info)
    duration = info.get("duration")
    live_status = info.get("live_status")
    return ProbeInfo(
        title=info.get("title"),
        duration_s=float(duration) if duration is not None else None,
        width=vf.get("width") or info.get("width"),
        height=vf.get("height") or info.get("height"),
        fps=float(vf["fps"]) if vf.get("fps") else (float(info["fps"]) if info.get("fps") else None),
        is_live=bool(info.get("is_live")) or live_status in ("is_live", "is_upcoming"),
        live_status=live_status,
        availability=info.get("availability"),
        age_limit=int(info.get("age_limit") or 0),
        thumbnail_url=info.get("thumbnail"),
    )


BAD_AVAILABILITY = {"private": "private", "premium_only": "members_only", "subscriber_only": "members_only",
                    "needs_auth": "needs_auth"}  # fmt: skip


def check_policy(p: ProbeInfo, max_duration_s: int = MAX_DURATION_S) -> None:
    """Same rules as the API preview (defence in depth: the download task never trusts the caller)."""
    if p.is_live or p.live_status in ("is_live", "is_upcoming", "post_live"):
        raise IngestError("live", f"live_status={p.live_status}")
    if p.availability in BAD_AVAILABILITY:
        raise IngestError(BAD_AVAILABILITY[p.availability], f"availability={p.availability}")
    if p.age_limit >= 18:
        raise IngestError("age_restricted", f"age_limit={p.age_limit}")
    if p.duration_s is None:
        raise IngestError("unknown_duration")
    if p.duration_s > max_duration_s:
        raise IngestError("too_long", f"{p.duration_s:.0f}s > {max_duration_s}s")


def probe(url: str, ydl_factory: YdlFactory = default_ydl_factory) -> ProbeInfo:
    canonical = canonical_youtube_url(url)
    try:
        with ydl_factory(ydl_options()) as ydl:
            info = ydl.extract_info(canonical, download=False)
    except Exception as e:  # yt-dlp raises DownloadError/ExtractorError with YouTube's message
        raise classify_error(e) from None
    if not info:
        raise IngestError("unavailable", "no metadata")
    return probe_info(info)


@dataclass(frozen=True)
class Downloaded:
    path: Path
    probe: ProbeInfo


def download(
    url: str, workdir: Path, ydl_factory: YdlFactory = default_ydl_factory, max_duration_s: int = MAX_DURATION_S
) -> Downloaded:
    """Probe → policy → download + merge into workdir/source.mp4 (needs ffmpeg on PATH)."""
    canonical = canonical_youtube_url(url)
    try:
        with ydl_factory(ydl_options(workdir)) as ydl:
            info = ydl.extract_info(canonical, download=False)
            if not info:
                raise IngestError("unavailable", "no metadata")
            meta = probe_info(info)
            check_policy(meta, max_duration_s)
            result = ydl.process_ie_result(info, download=True)
    except Exception as e:
        raise classify_error(e) from None
    path = _downloaded_path(result, workdir)
    if path is None or not path.is_file() or path.stat().st_size == 0:
        raise IngestError("download_failed", "yt-dlp produced no file")
    return Downloaded(path, meta)


def _downloaded_path(result: Mapping[str, Any], workdir: Path) -> Path | None:
    for d in result.get("requested_downloads") or ():
        if fp := d.get("filepath"):
            return Path(fp)
    if fp := result.get("filepath"):
        return Path(fp)
    files = sorted(p for p in workdir.glob("source.*") if p.suffix not in (".part", ".ytdl"))
    return files[0] if files else None


# ---------------------------------------------------------------- ffprobe
@dataclass(frozen=True)
class MediaInfo:
    duration_ms: int | None
    width: int | None
    height: int | None
    fps: float | None
    vcodec: str | None


def parse_ffprobe(data: Mapping[str, Any]) -> MediaInfo:
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        raise IngestError("unreadable", "no video stream")
    fps: float | None = None
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw = video.get(key)
        if raw and raw not in ("0/0", "0"):
            try:
                fps = round(float(Fraction(raw)), 3)
                break
            except (ValueError, ZeroDivisionError):
                continue
    duration = (data.get("format") or {}).get("duration") or video.get("duration")
    return MediaInfo(
        duration_ms=round(float(duration) * 1000) if duration else None,
        width=video.get("width"),
        height=video.get("height"),
        fps=fps,
        vcodec=video.get("codec_name"),
    )


def ffprobe(path: Path, timeout_s: float = 120) -> MediaInfo:
    cmd = ["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-print_format", "json", "-show_format",
           "-show_streams", str(path)]  # fmt: skip
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=True,
                             stdin=subprocess.DEVNULL, preexec_fn=child_limits(PROBE_MAX_MEM_MB, max_file_mb=0)).stdout
    except FileNotFoundError:
        raise IngestError("download_failed", "ffprobe not installed") from None
    except subprocess.TimeoutExpired:
        raise IngestError("unreadable", "ffprobe timed out") from None
    except subprocess.CalledProcessError as e:
        raise IngestError("unreadable", (e.stderr or "")[:300]) from None
    return parse_ffprobe(json.loads(out))
