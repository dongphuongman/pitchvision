"""Stage S2 normalise (AD §6.2): original → ``normalized.mp4`` on the 25 fps analysis timeline.

Output contract (everything downstream — VideoReader, tiles, the browser's ``round(mediaTime × 25)`` — relies on it):

- H.264 High, yuv420p, constant ``target_fps`` (25). 50/60/29.97 fps sources are **resampled by frame
  selection** (ffmpeg ``fps`` filter, nearest frame, never blended), so output frame ``k`` is the source frame
  nearest ``k / 25`` s — the same rule ``cv_core.io.VideoReader(target_fps=25)`` applies to the original;
- long side ``long_side`` (1280), aspect kept, even dims; sources already smaller are not upscaled;
- closed GOP of exactly ``keyframe_interval_s`` × fps frames (2 s → 50): ``-g 50 -keyint_min 50 -sc_threshold 0``
  plus forced keyframes, so a seek lands on a keyframe every 2 s (tile size = 10 s = 5 GOPs);
- first frame at pts 0; ``+faststart`` (moov first) for progressive playback from R2/CDN;
- audio: first audio stream → AAC-LC mono 96 kb/s (kept for S11 export / the player; dropped when absent
  or when the codec is not allow-listed).

Hardening (AD §14, "file độc hại"): ffprobe/ffmpeg run with ``-nostdin``, a timeout and a local-file-only
protocol whitelist and rlimits (``cv_core.io.limits``: memory, no core dumps, probes write no files); containers
and codecs must be allow-listed; absurd dimensions/durations/frame rates are rejected before any decode.
Unprivileged container + seccomp: infra/compose (M1-BE-22).

Encoders: ``auto`` = ``h264_nvenc`` when ffmpeg has it and an NVIDIA GPU is visible, else ``libx264``;
``videotoolbox`` (Mac) is opt-in only (its rate control is bitrate-based, not CRF).
"""
from __future__ import annotations

import json
import logging
import math
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from fractions import Fraction
from functools import cache
from pathlib import Path
from typing import Any, Literal

from cv_core.io.limits import FFMPEG_MAX_MEM_MB, PROBE_MAX_MEM_MB, child_limits

log = logging.getLogger(__name__)

PathLike = str | os.PathLike[str]
Encoder = Literal["auto", "libx264", "h264_nvenc", "videotoolbox"]

# --- allow-lists / limits ------------------------------------------------------------------------------------------
ALLOWED_CONTAINERS = frozenset({"mov", "mp4", "m4a", "3gp", "3g2", "mj2", "matroska", "webm", "mpegts", "flv"})
ALLOWED_VIDEO_CODECS = frozenset({"h264", "hevc", "vp8", "vp9", "av1", "mpeg2video", "mpeg4", "prores"})
ALLOWED_AUDIO_CODECS = frozenset({"aac", "mp3", "mp2", "opus", "vorbis", "ac3", "eac3", "flac", "alac",
                                  "pcm_s16le", "pcm_s24le", "pcm_f32le"})
MAX_LONG_SIDE = 7680          # 8K UHD
MIN_SHORT_SIDE = 144
MAX_PIXELS = 7680 * 4320
MAX_DURATION_S = 4 * 3600     # a match with extra time + pre/post show fits easily
MIN_DURATION_S = 1.0
MIN_FPS, MAX_FPS = 10.0, 240.0
PROBE_TIMEOUT_S = 30.0


class MediaError(ValueError):
    """Input rejected (not a video we accept). Not retryable: the same file fails the same way."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class FFmpegError(RuntimeError):
    """ffmpeg/ffprobe failed or timed out (possibly transient: retryable)."""


@dataclass(frozen=True, slots=True)
class MediaInfo:
    """ffprobe summary of the first video stream (+ first audio stream, if any)."""

    container: str
    video_codec: str
    width: int                 # display size (rotation applied)
    height: int
    fps: float                 # average frame rate
    fps_fraction: Fraction
    duration_ms: int
    frame_count: int           # container count when known, else round(duration × fps)
    rotation: int = 0
    pix_fmt: str | None = None
    audio_codec: str | None = None
    audio_channels: int | None = None
    bit_rate: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def manifest_fields(self) -> dict[str, int]:
        """Partial ``pitchvision_schemas.Manifest`` fields: fps, width, height, duration_ms, frame_count."""
        return {"fps": round(self.fps), "width": self.width, "height": self.height,
                "duration_ms": self.duration_ms, "frame_count": self.frame_count}


@dataclass(frozen=True, slots=True)
class NormalizeResult:
    path: Path
    source: MediaInfo
    output: MediaInfo
    encoder: str
    elapsed_s: float
    command: tuple[str, ...] = field(repr=False)

    def manifest_fields(self) -> dict[str, int]:
        return self.output.manifest_fields()

    @property
    def speed(self) -> float:
        """Media seconds normalised per wall-clock second (× realtime)."""
        return (self.source.duration_ms / 1000) / self.elapsed_s if self.elapsed_s > 0 else math.inf


# --- tools ---------------------------------------------------------------------------------------------------------
def _tool(name: str) -> str:
    exe = shutil.which(os.environ.get(f"PITCHVISION_{name.upper()}", name))
    if exe is None:
        raise FFmpegError(f"{name} not found on PATH")
    return exe


def _run(argv: list[str], timeout: float, limits: Callable[[], None] | None = None
         ) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False,
                              stdin=subprocess.DEVNULL, preexec_fn=limits)
    except subprocess.TimeoutExpired as e:
        raise FFmpegError(f"{Path(argv[0]).name} timed out after {timeout:.0f}s") from e


@cache
def ffmpeg_encoders() -> frozenset[str]:
    out = _run([_tool("ffmpeg"), "-hide_banner", "-nostdin", "-encoders"], 30)
    names = set()
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
            names.add(parts[1])
    return frozenset(names)


def select_encoder(encoder: Encoder = "auto") -> str:
    """ffmpeg encoder name for ``encoder`` (``auto``: NVENC on NVIDIA, else libx264)."""
    if encoder == "videotoolbox":
        return "h264_videotoolbox"
    if encoder != "auto":
        return encoder
    if "h264_nvenc" in ffmpeg_encoders():
        from cv_core.runtime.providers import cuda_device_present

        if cuda_device_present():
            return "h264_nvenc"
    return "libx264"


# --- probe ---------------------------------------------------------------------------------------------------------
def _fraction(s: str | None) -> Fraction | None:
    if not s or s in ("0/0", "N/A"):
        return None
    try:
        f = Fraction(s)
    except (ValueError, ZeroDivisionError):
        return None
    return f if f > 0 else None


def _rotation(stream: dict[str, Any]) -> int:
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            return round(float(sd["rotation"])) % 360
    tag = (stream.get("tags") or {}).get("rotate")
    return int(tag) % 360 if tag and tag.lstrip("-").isdigit() else 0


def probe(src: PathLike, *, timeout: float = PROBE_TIMEOUT_S, validate: bool = True) -> MediaInfo:
    """ffprobe ``src`` (local file) and, with ``validate``, enforce the allow-lists / limits (``MediaError``)."""
    path = Path(src)
    if not path.is_file():
        raise MediaError("NOT_A_FILE", f"{path} does not exist or is not a regular file")
    argv = [_tool("ffprobe"), "-v", "error", "-protocol_whitelist", "file",
            "-show_format", "-show_streams", "-of", "json", str(path)]
    out = _run(argv, timeout, child_limits(PROBE_MAX_MEM_MB, max_file_mb=0))
    if out.returncode != 0:
        raise MediaError("UNREADABLE_MEDIA", (out.stderr.strip() or "ffprobe failed")[:500])
    try:
        data = json.loads(out.stdout or "{}")
    except json.JSONDecodeError as e:
        raise MediaError("UNREADABLE_MEDIA", f"ffprobe output is not JSON: {e}") from e
    info = _parse_probe(data)
    if validate:
        validate_media(info)
    return info


def _parse_probe(data: dict[str, Any]) -> MediaInfo:
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    if video is None:
        raise MediaError("NO_VIDEO_STREAM", "no video stream")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fps = _fraction(video.get("avg_frame_rate")) or _fraction(video.get("r_frame_rate"))
    if fps is None:
        raise MediaError("UNKNOWN_FRAME_RATE", "cannot determine the frame rate")
    dur_s = None
    for d in (video.get("duration"), fmt.get("duration")):
        try:
            dur_s = float(d) if d not in (None, "N/A") else None
        except ValueError:
            dur_s = None
        if dur_s:
            break
    if not dur_s:
        raise MediaError("UNKNOWN_DURATION", "cannot determine the duration")
    w, h = int(video.get("width") or 0), int(video.get("height") or 0)
    rot = _rotation(video)
    if rot in (90, 270):
        w, h = h, w
    nb = video.get("nb_frames")
    frames = int(nb) if nb and str(nb).isdigit() and int(nb) > 0 else round(dur_s * float(fps))
    br = fmt.get("bit_rate")
    return MediaInfo(
        container=str(fmt.get("format_name") or ""), video_codec=str(video.get("codec_name") or ""),
        width=w, height=h, fps=float(fps), fps_fraction=fps, duration_ms=round(dur_s * 1000), frame_count=frames,
        rotation=rot, pix_fmt=video.get("pix_fmt"),
        audio_codec=audio.get("codec_name") if audio else None,
        audio_channels=int(audio["channels"]) if audio and audio.get("channels") else None,
        bit_rate=int(br) if br and str(br).isdigit() else None, raw=data,
    )


def validate_media(info: MediaInfo) -> None:
    """Allow-listed container + codec, sane size / duration / frame rate, else ``MediaError``."""
    if not set(info.container.split(",")) & ALLOWED_CONTAINERS:
        raise MediaError("UNSUPPORTED_CONTAINER", f"container {info.container!r} is not accepted")
    if info.video_codec not in ALLOWED_VIDEO_CODECS:
        raise MediaError("UNSUPPORTED_CODEC", f"video codec {info.video_codec!r} is not accepted")
    long_side, short_side = max(info.width, info.height), min(info.width, info.height)
    if short_side < MIN_SHORT_SIDE or long_side > MAX_LONG_SIDE or info.width * info.height > MAX_PIXELS:
        raise MediaError("BAD_DIMENSIONS", f"{info.width}×{info.height} is outside the accepted range")
    if not MIN_FPS <= info.fps <= MAX_FPS:
        raise MediaError("BAD_FRAME_RATE", f"{info.fps:.3f} fps is outside [{MIN_FPS}, {MAX_FPS}]")
    if not MIN_DURATION_S * 1000 <= info.duration_ms <= MAX_DURATION_S * 1000:
        raise MediaError("BAD_DURATION",
                         f"{info.duration_ms / 1000:.1f}s is outside [{MIN_DURATION_S}, {MAX_DURATION_S}]s")


# --- normalise -----------------------------------------------------------------------------------------------------
def target_size(width: int, height: int, long_side: int) -> tuple[int, int]:
    """(w, h) with max side = min(long_side, source long side), aspect kept, both even (≥ 2)."""
    ls = min(long_side, max(width, height))
    s = ls / max(width, height)
    return max(2, round(width * s / 2) * 2), max(2, round(height * s / 2) * 2)


def _encoder_args(enc: str, crf: int, preset: str | None, gop: int) -> list[str]:
    common = ["-g", str(gop), "-keyint_min", str(gop), "-pix_fmt", "yuv420p", "-profile:v", "high"]
    if enc == "libx264":
        return ["-c:v", "libx264", "-preset", preset or "veryfast", "-crf", str(crf), "-sc_threshold", "0",
                "-x264-params", "open-gop=0", *common]
    if enc == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", preset or "p4", "-rc", "vbr", "-cq", str(crf), "-b:v", "0",
                "-no-scenecut", "1", "-strict_gop", "1", "-forced-idr", "1", *common]
    if enc == "h264_videotoolbox":
        # no CRF: ~6 Mb/s is visually comparable to x264 CRF 21 at 1280×720 25 fps broadcast
        return ["-c:v", "h264_videotoolbox", "-b:v", "6M", "-maxrate", "9M", "-bufsize", "12M", "-realtime", "0",
                *common]
    raise ValueError(f"unknown encoder {enc!r}")


def _select_filter(src_fps: Fraction, fps: int) -> str:
    """Frame-selection resampler: output slot ``k`` gets the source frame nearest ``k / fps`` (ties → earlier).

    ffmpeg's ``fps`` filter (round=near) gives slot ``k`` the *last* frame with ``round(pts × fps) == k`` — i.e. the
    last frame with ``pts < (k + ½) / fps`` — which is not always the nearest (29.97 → 25 picks the later of two
    near-tied frames). The nearest frame is the last one with ``pts < k / fps + 1 / (2 · src_fps)``, so shifting
    pts by ``δ = 1 / (2 · fps) − 1 / (2 · src_fps)`` before the filter turns its rule into "nearest", matching
    ``VideoReader(target_fps=…)`` on the original. Output pts are the slot times (exact k / fps), δ does not leak.
    Timeline 0 = first video frame (``PTS-STARTPTS``).
    """
    delta = Fraction(1, 2 * fps) - 1 / (2 * src_fps)
    return f"setpts=PTS-STARTPTS+{float(delta):.9f}/TB,fps=fps={fps}:round=near"


def frame_select_filter(src_fps: Fraction, fps: int) -> str:
    """Public name of S2's frame selection (S11 renders 1080p exports from the original on the same frame grid)."""
    return _select_filter(src_fps, fps)


def build_command(src: Path, dst: Path, info: MediaInfo, *, fps: int, long_side: int, keyframe_interval_s: int,
                  encoder: str, crf: int, preset: str | None, audio: bool, threads: int | None) -> list[str]:
    w, h = target_size(info.width, info.height, long_side)
    gop = fps * keyframe_interval_s
    vf = f"{_select_filter(info.fps_fraction, fps)},scale={w}:{h}:flags=area,setsar=1,format=yuv420p"
    argv = [_tool("ffmpeg"), "-hide_banner", "-nostdin", "-y", "-v", "error", "-protocol_whitelist", "file",
            "-i", str(src), "-map", "0:v:0", "-vf", vf, "-fps_mode", "cfr", "-r", str(fps),
            *_encoder_args(encoder, crf, preset, gop),
            "-force_key_frames", f"expr:eq(mod(n,{gop}),0)"]
    if threads:
        argv += ["-threads", str(threads)]
    keep_audio = audio and info.audio_codec in ALLOWED_AUDIO_CODECS
    if audio and info.audio_codec and not keep_audio:
        log.warning("dropping audio: codec %r not allow-listed", info.audio_codec)
    if keep_audio:
        argv += ["-map", "0:a:0", "-af", "asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0",
                 "-c:a", "aac", "-b:a", "96k", "-ac", "1", "-ar", "48000"]
    else:
        argv += ["-an"]
    argv += ["-sn", "-dn", "-map_metadata", "-1", "-map_chapters", "-1", "-movflags", "+faststart",
             "-video_track_timescale", str(fps * 1000), "-f", "mp4", str(dst)]
    return argv


def _ffmpeg_limits(enc: str) -> Callable[[], None] | None:
    return child_limits(None if enc == "h264_nvenc" else FFMPEG_MAX_MEM_MB)  # CUDA reserves huge address ranges


def normalize(src: PathLike, dst: PathLike, *, fps: int = 25, long_side: int = 1280, keyframe_interval_s: int = 2,
              encoder: Encoder = "auto", crf: int = 21, preset: str | None = None, audio: bool = True,
              threads: int | None = None, timeout_s: float | None = None) -> NormalizeResult:
    """Normalise ``src`` → ``dst`` (mp4). Raises ``MediaError`` (bad input) or ``FFmpegError`` (tool failure).

    ``timeout_s`` default: 120 s + 2 × duration (software x264 'veryfast' at 1280px runs ≫ 2× realtime on 4 cores).
    The output is written to ``dst.partial`` then renamed, so a crash never leaves a truncated ``normalized.mp4``.
    """
    src_p, dst_p = Path(src), Path(dst)
    info = probe(src_p)
    enc = select_encoder(encoder)
    timeout = timeout_s if timeout_s is not None else 120 + 2 * info.duration_ms / 1000
    tmp = dst_p.with_name(dst_p.name + ".partial")
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    argv = build_command(src_p, tmp, info, fps=fps, long_side=long_side, keyframe_interval_s=keyframe_interval_s,
                         encoder=enc, crf=crf, preset=preset, audio=audio, threads=threads)
    t0 = time.perf_counter()
    try:
        out = _run(argv, timeout, _ffmpeg_limits(enc))
        if out.returncode != 0 and enc != "libx264" and encoder == "auto":
            log.warning("%s failed (%s); retrying with libx264", enc, out.stderr.strip()[-300:])
            enc = "libx264"
            argv = build_command(src_p, tmp, info, fps=fps, long_side=long_side,
                                 keyframe_interval_s=keyframe_interval_s, encoder=enc, crf=crf, preset=None,
                                 audio=audio, threads=threads)
            out = _run(argv, timeout, _ffmpeg_limits(enc))
        if out.returncode != 0:
            raise FFmpegError(f"ffmpeg exited {out.returncode}: {out.stderr.strip()[-1000:]}")
        os.replace(tmp, dst_p)
    finally:
        tmp.unlink(missing_ok=True)
    elapsed = time.perf_counter() - t0
    result_info = probe(dst_p, validate=False)
    if round(result_info.fps) != fps:
        raise FFmpegError(f"normalised output has {result_info.fps:.3f} fps, expected {fps}")
    log.info("normalised %s → %s (%s, %dx%d, %d frames) in %.1fs", src_p.name, dst_p.name, enc,
             result_info.width, result_info.height, result_info.frame_count, elapsed)
    return NormalizeResult(path=dst_p, source=info, output=result_info, encoder=enc, elapsed_s=elapsed,
                           command=tuple(argv))
