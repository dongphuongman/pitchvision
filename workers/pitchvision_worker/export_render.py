"""S11 render (AD §6 "Theo yêu cầu", M1-BE-17): normalized.mp4 + tracks.parquet + ``ExportSpec`` → annotated MP4.

ffmpeg decodes the range of normalized.mp4 (through a presigned URL, input-seeking) straight at the output size, each
frame is painted in numpy / OpenCV and piped to a second ffmpeg that encodes H.264 (yuv420p, faststart) and copies
the range's audio. Output frame ``i`` is normalized frame ``f0 + i`` — the same frame grid as tracks.parquet and the
tiles, so the drawing matches what the workspace shows.

Drawing follows the web overlay (apps/web/src/player/overlay.ts): team-coloured boxes (referee dashed yellow),
``#id`` / ``GK #id`` labels (+ confidence), the ball as a white dot, optional trails (last second, bottom-centre of
players / centre of the ball). ``layout = video_map`` adds a 2D pitch panel on the right (px / py in metres;
objects without pitch coordinates are skipped). Free plan: a translucent "PitchVision" watermark bottom-right.

Sharper exports (``out_height`` above the normalized copy, i.e. 1080p): frames come from the ORIGINAL upload when
the task passes ``source_url``, put on the normalized timeline with S2's own frame selection
(``cv_core.io.normalize.frame_select_filter``: 0 = first video frame, slot k = source frame nearest k / fps) after
an input seek whose offset is added back — frame ``f0 + i`` is exactly the frame the tracks were computed on.
Without a larger source the normalized copy is upscaled. OpenCV's Hershey font is not the UI's Inter.
"""

from __future__ import annotations

import json
import logging
import math
import shutil
import subprocess
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from itertools import pairwise
from pathlib import Path
from typing import IO, Any

import cv2
import numpy as np
import pyarrow.compute as pc
from cv_core.io.limits import FFMPEG_MAX_MEM_MB, PROBE_MAX_MEM_MB, child_limits
from cv_core.io.normalize import frame_select_filter
from cv_core.io.parquet import read_tracks
from cv_core.runtime.model import default_intra_op_threads
from pitchvision_schemas import ExportSpec, ObjCls, load_pitch_template

log = logging.getLogger(__name__)

REFEREE_COLOR = "#f5d90a"
UNKNOWN_COLOR = "#9aa4b2"
LABEL_BG = (13, 9, 5)  # BGR of rgba(5,9,13)
PITCH_BG = (52, 94, 36)
PITCH_MARGIN_M = 4.0
TRAIL_S = 1.0
TRAIL_MAX_STEP = 0.06  # × frame height: a longer hop between frames is a detection jump, not motion
PROGRESS_EVERY_S = 2.0
DECODER_WAIT_S = 30.0
ENCODER_WAIT_S = 600.0  # flushing x264's lookahead + muxing the audio + faststart rewrite
FONT = cv2.FONT_HERSHEY_SIMPLEX
WATERMARK = "PitchVision"


class RenderError(Exception):
    """Permanent / transient render failure → exports.error ``{code, retryable, detail}``."""

    def __init__(self, code: str, detail: str, *, retryable: bool) -> None:
        super().__init__(detail)
        self.code, self.detail, self.retryable = code, detail, retryable


class RenderCancelled(Exception):
    """The progress callback said stop (export cancelled / deleted)."""


@dataclass(frozen=True)
class RenderResult:
    frames: int
    width: int
    height: int
    seconds: float
    from_source: bool = False


def hex_bgr(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[4:6], 16), int(c[2:4], 16), int(c[0:2], 16)


def even(x: float) -> int:
    return max(2, 2 * round(x / 2))


def frame_range(spec: ExportSpec) -> tuple[int, int]:
    """[f0, f1) on the normalized frame grid: first frame at / after from_ms … first frame at / after to_ms."""
    return -(-spec.from_ms * spec.fps // 1000), -(-spec.to_ms * spec.fps // 1000)


# ---------------------------------------------------------------- tracks
@dataclass
class TrackIndex:
    frame_idx: np.ndarray
    cols: dict[str, np.ndarray]

    def rows(self, f: int) -> dict[str, np.ndarray]:
        a, b = np.searchsorted(self.frame_idx, [f, f + 1])
        return {k: v[a:b] for k, v in self.cols.items()}


def load_tracks(path: Path, f0: int, f1: int) -> TrackIndex:
    t = read_tracks(path)
    fi = t.column("frame_idx")
    t = t.filter(pc.and_(pc.greater_equal(fi, f0), pc.less(fi, f1)))
    t = t.sort_by([("frame_idx", "ascending"), ("track_id", "ascending")])
    names = ("track_id", "cls", "team", "x1", "y1", "x2", "y2", "conf", "px", "py")
    cols = {n: t.column(n).to_numpy(zero_copy_only=False) for n in names}
    return TrackIndex(t.column("frame_idx").to_numpy(zero_copy_only=False).astype(np.int64), cols)


# ---------------------------------------------------------------- painting
class Painter:
    """Draws one frame's objects (in normalized pixel space ``spec.width × height``) onto an output-size frame."""

    def __init__(self, spec: ExportSpec, video_w: int, video_h: int) -> None:
        self.spec, self.layers = spec, spec.layers
        self.s = video_h / spec.height
        self.video_w, self.video_h = video_w, video_h
        self.team = {t.cluster: hex_bgr(t.color) for t in spec.teams}
        self.fixes = {o.track_id: (o.team, o.cls) for o in spec.track_overrides}  # SCR-14 E06
        self.referee, self.unknown = hex_bgr(REFEREE_COLOR), hex_bgr(UNKNOWN_COLOR)
        self.font_scale = 0.5 * max(1.0, video_h / 720)
        self.thick = max(2, round(2 * video_h / 720))
        self.trail_len = max(2, round(TRAIL_S * spec.fps))
        self.trails: dict[int, deque[tuple[int, int, int]]] = {}
        self.map_bg: np.ndarray | None = None
        if spec.layout == "video_map":
            self.map_bg, self.map_scale = self._pitch(video_h)
        self.panel_w = 0 if self.map_bg is None else self.map_bg.shape[1]
        self.width = video_w + self.panel_w
        self.wm = self._watermark() if spec.watermark else None

    def color(self, cls: int, team: int) -> tuple[int, int, int]:
        if cls == ObjCls.referee:
            return self.referee
        if cls == ObjCls.ball:
            return (255, 255, 255)
        return self.team.get(int(team), self.unknown)

    # ---- static layers
    def _pitch(self, h: int) -> tuple[np.ndarray, float]:
        tpl = load_pitch_template()
        scale = h / (tpl.width_m + 2 * PITCH_MARGIN_M)
        w = even((tpl.length_m + 2 * PITCH_MARGIN_M) * scale)
        img = np.empty((h, w, 3), np.uint8)
        img[:] = PITCH_BG
        lw = max(1, round(h / 360))
        for line in tpl.lines:
            pts = np.array([self._m2px(x, y, scale) for x, y in line.points], np.int32)
            if line.kind == "spot":
                for p in pts:
                    cv2.circle(img, tuple(int(v) for v in p), lw + 1, (235, 235, 235), -1, cv2.LINE_AA)
            elif len(pts) >= 2:
                cv2.polylines(img, [pts], line.closed, (235, 235, 235), lw, cv2.LINE_AA)
        return img, scale

    @staticmethod
    def _m2px(x: float, y: float, scale: float) -> tuple[int, int]:
        return round((x + PITCH_MARGIN_M) * scale), round((y + PITCH_MARGIN_M) * scale)

    def _watermark(self) -> tuple[np.ndarray, np.ndarray, int, int]:
        """(BGR patch, alpha 0..1, x, y) for the bottom-right corner."""
        scale = 0.9 * self.video_h / 720
        thick = max(2, round(2 * self.video_h / 720))
        (tw, th), base = cv2.getTextSize(WATERMARK, FONT, scale, thick)
        pad = round(10 * self.video_h / 720)
        w, h = tw + 2 * pad, th + base + 2 * pad
        patch = np.zeros((h, w, 3), np.uint8)
        mask = np.zeros((h, w), np.uint8)
        cv2.putText(mask, WATERMARK, (pad, pad + th), FONT, scale, 255, thick + 2, cv2.LINE_AA)
        cv2.putText(patch, WATERMARK, (pad, pad + th), FONT, scale, (255, 255, 255), thick, cv2.LINE_AA)
        alpha = (mask.astype(np.float32) / 255.0 * 0.55)[..., None]
        x, y = self.video_w - w - pad, self.video_h - h - pad
        return patch, alpha, x, y

    # ---- per frame
    def _fix(self, rows: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        """User fixes (manifest track_overrides) on top of the detected team / class, like the web player."""
        if not self.fixes or not len(rows["track_id"]):
            return rows
        hit = [i for i, tid in enumerate(rows["track_id"].tolist()) if tid in self.fixes]
        if not hit:
            return rows
        rows = {**rows, "team": rows["team"].copy(), "cls": rows["cls"].copy()}
        for i in hit:
            rows["team"][i], rows["cls"][i] = self.fixes[int(rows["track_id"][i])]
        return rows

    def paint(self, frame: np.ndarray, f: int, rows: dict[str, np.ndarray]) -> np.ndarray:
        s, L = self.s, self.layers
        rows = self._fix(rows)
        n = len(rows["track_id"])
        if L.trails:
            self._trails(frame, f, rows)
        labels: list[tuple[int, int, str, tuple[int, int, int]]] = []
        for i in range(n):
            cls, team, tid = int(rows["cls"][i]), int(rows["team"][i]), int(rows["track_id"][i])
            x1, y1 = round(float(rows["x1"][i]) * s), round(float(rows["y1"][i]) * s)
            x2, y2 = round(float(rows["x2"][i]) * s), round(float(rows["y2"][i]) * s)
            if cls == ObjCls.ball:
                if L.ball:
                    r = max(4, round(min(x2 - x1, y2 - y1) / 2) + 2)
                    c = ((x1 + x2) // 2, (y1 + y2) // 2)
                    cv2.circle(frame, c, r, (255, 255, 255), -1, cv2.LINE_AA)
                    cv2.circle(frame, c, r, (20, 15, 11), self.thick, cv2.LINE_AA)
                continue
            color = self.color(cls, team)
            if L.boxes:
                if cls == ObjCls.referee:
                    _dashed_rect(frame, (x1, y1), (x2, y2), color, self.thick)
                else:
                    cv2.rectangle(frame, (x1, y1), (x2, y2), color, self.thick, cv2.LINE_AA)
            if L.labels:
                text = f"{'GK ' if cls == ObjCls.goalkeeper else ''}#{tid}"
                if L.conf:
                    text += f" {float(rows['conf'][i]):.2f}"
                labels.append((x1, y1 - 3, text, color))
        for x, y, text, color in labels:  # labels last so boxes never cover them
            (tw, th), base = cv2.getTextSize(text, FONT, self.font_scale, 1)
            y = max(th + base + 1, y)
            cv2.rectangle(frame, (x - 1, y - th - base - 2), (x + tw + 6, y), LABEL_BG, -1)
            cv2.putText(frame, text, (x + 3, y - base), FONT, self.font_scale,
                        _text_color(color, self.unknown), 1, cv2.LINE_AA)  # fmt: skip
        if self.wm is not None:
            patch, alpha, x, y = self.wm
            roi = frame[y:y + patch.shape[0], x:x + patch.shape[1]]
            roi[:] = (roi * (1 - alpha) + patch * alpha).astype(np.uint8)
        if self.map_bg is None:
            return frame
        return np.concatenate([frame, self._map(rows)], axis=1)

    def _trails(self, frame: np.ndarray, f: int, rows: dict[str, np.ndarray]) -> None:
        s = self.s
        seen = set()
        for i in range(len(rows["track_id"])):
            cls, tid = int(rows["cls"][i]), int(rows["track_id"][i])
            if cls == ObjCls.ball and not self.layers.ball:
                continue
            x = round((float(rows["x1"][i]) + float(rows["x2"][i])) / 2 * s)
            y = round((float(rows["y1"][i]) + float(rows["y2"][i])) / 2 * s if cls == ObjCls.ball
                      else float(rows["y2"][i]) * s)  # fmt: skip
            q = self.trails.setdefault(tid, deque(maxlen=self.trail_len))
            q.append((f, x, y))
            seen.add(tid)
            pts = [(px, py) for pf, px, py in q if f - pf < self.trail_len]
            if len(pts) >= 2:
                color = self.color(cls, int(rows["team"][i]))
                for seg in split_jumps(pts, TRAIL_MAX_STEP * self.video_h):
                    if len(seg) >= 2:
                        cv2.polylines(frame, [np.array(seg, np.int32)], False, color, max(1, self.thick - 1),
                                      cv2.LINE_AA)  # fmt: skip
        for tid in [t for t in self.trails if t not in seen and f - self.trails[t][-1][0] >= self.trail_len]:
            del self.trails[tid]

    def _map(self, rows: dict[str, np.ndarray]) -> np.ndarray:
        assert self.map_bg is not None
        img = self.map_bg.copy()
        r = max(4, round(self.video_h / 110))
        order = np.argsort(rows["cls"] == ObjCls.ball, kind="stable")  # ball on top
        for i in order:
            px, py = float(rows["px"][i]), float(rows["py"][i])
            if not (math.isfinite(px) and math.isfinite(py)):
                continue
            cls = int(rows["cls"][i])
            if cls == ObjCls.ball and not self.layers.ball:
                continue
            c = self._m2px(px, py, self.map_scale)
            if cls == ObjCls.ball:
                cv2.circle(img, c, max(3, r - 2), (255, 255, 255), -1, cv2.LINE_AA)
                cv2.circle(img, c, max(3, r - 2), (20, 15, 11), 1, cv2.LINE_AA)
            else:
                cv2.circle(img, c, r, self.color(cls, int(rows["team"][i])), -1, cv2.LINE_AA)
                cv2.circle(img, c, r, (20, 15, 11), 1, cv2.LINE_AA)
        return img


def _text_color(color: tuple[int, int, int], unknown: tuple[int, int, int]) -> tuple[int, int, int]:
    """Team colour on the dark label, white when that colour is too dark to read (or unknown)."""
    b, g, r = color
    return (255, 255, 255) if color == unknown or 0.299 * r + 0.587 * g + 0.114 * b < 110 else color


def split_jumps(pts: list[tuple[int, int]], max_step: float) -> list[list[tuple[int, int]]]:
    """Cut a trail where consecutive points are farther apart than ``max_step`` px (ball re-detected elsewhere,
    id switch) so no straight line is drawn across the frame."""
    out: list[list[tuple[int, int]]] = [[pts[0]]] if pts else []
    for a, b in pairwise(pts):
        if math.hypot(b[0] - a[0], b[1] - a[1]) > max_step:
            out.append([b])
        else:
            out[-1].append(b)
    return out


def _dashed_rect(img: np.ndarray, p1: tuple[int, int], p2: tuple[int, int], color: tuple[int, int, int],
                 thick: int, dash: int = 6, gap: int = 4) -> None:  # fmt: skip
    (x1, y1), (x2, y2) = p1, p2
    for a, b in (((x1, y1), (x2, y1)), ((x2, y1), (x2, y2)), ((x2, y2), (x1, y2)), ((x1, y2), (x1, y1))):
        length = max(abs(b[0] - a[0]), abs(b[1] - a[1]))
        for t0 in range(0, max(1, length), dash + gap):
            t1 = min(length, t0 + dash)
            u0, u1 = t0 / max(1, length), t1 / max(1, length)
            q0 = (round(a[0] + (b[0] - a[0]) * u0), round(a[1] + (b[1] - a[1]) * u0))
            q1 = (round(a[0] + (b[0] - a[0]) * u1), round(a[1] + (b[1] - a[1]) * u1))
            cv2.line(img, q0, q1, color, thick, cv2.LINE_AA)


# ---------------------------------------------------------------- ffmpeg pipeline
def _read_exact(stream: IO[bytes], buf: memoryview) -> bool:
    got = 0
    while got < len(buf):
        n = stream.readinto(buf[got:])  # type: ignore[attr-defined]
        if not n:
            return False
        got += n
    return True


def _stderr(p: subprocess.Popen[bytes], timeout_s: float) -> str:
    """Wait for exit (``-loglevel error`` keeps stderr far below the pipe buffer) → its stderr tail."""
    try:
        p.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill(p)
        return "timed out"
    return (p.stderr.read() if p.stderr else b"").decode(errors="replace")[-500:]


def _kill(*procs: subprocess.Popen[Any] | None) -> None:
    for p in procs:
        if p is not None and p.poll() is None:
            p.kill()
            p.wait()


@dataclass(frozen=True)
class SourceInfo:
    fps: Fraction
    start_s: float  # pts of the first video frame (timeline 0 of normalized.mp4)
    height: int


def probe_source(url: str, ffprobe: str | None = None, timeout_s: float = 30.0) -> SourceInfo | None:
    exe = ffprobe or shutil.which("ffprobe") or "ffprobe"
    cmd = [exe, "-v", "error", "-protocol_whitelist", "file,http,https,tcp,tls", "-select_streams", "v:0",
           "-show_entries", "stream=avg_frame_rate,r_frame_rate,start_time,height", "-of", "json", url]  # fmt: skip
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout_s, check=True, stdin=subprocess.DEVNULL,
                             preexec_fn=child_limits(PROBE_MAX_MEM_MB, max_file_mb=0)).stdout
        st = (json.loads(out).get("streams") or [{}])[0]
        rate = st.get("avg_frame_rate") or st.get("r_frame_rate") or "0/1"
        fps = Fraction(rate) if rate != "0/0" else Fraction(0)
        if fps <= 0:
            return None
        return SourceInfo(fps=fps, start_s=float(st.get("start_time") or 0.0), height=int(st.get("height") or 0))
    except (subprocess.SubprocessError, ValueError, ZeroDivisionError, OSError):
        return None


def _decoder_input(spec: ExportSpec, video_url: str, source_url: str | None, source: SourceInfo | None,
                   start_s: float, dur_s: float, w: int, h: int) -> tuple[list[str], bool]:
    """(ffmpeg input + filter args, used the original?)"""
    wl = ["-protocol_whitelist", "file,http,https,tcp,tls"]
    if source_url and source and source.height > spec.height:
        pre = max(0.0, start_s - 5.0)  # input-seek a bit before (keyframe), select on absolute pts
        # input -ss (relative to the stream start) rebases pts so that 0 = the seek point: + pre restores the
        # normalized timeline (0 = first video frame) before S2's frame selection
        vf = (f"{frame_select_filter(source.fps, spec.fps).replace('PTS-STARTPTS', f'PTS+{pre:.6f}/TB')},"
              f"trim=start={start_s:.6f}:end={start_s + dur_s:.6f},setpts=PTS-STARTPTS,"
              f"scale={w}:{h}:flags=lanczos")
        return [*wl, "-ss", f"{pre:.6f}", "-i", source_url, "-map", "0:v:0",
                "-vf", vf], True  # fmt: skip
    return [*wl, "-ss", f"{start_s:.3f}", "-i", video_url, "-t", f"{dur_s:.3f}", "-map", "0:v:0",
            "-vf", f"scale={w}:{h}:flags=bicubic"], False  # fmt: skip


def render(spec: ExportSpec, video_url: str, tracks_path: Path, out_path: Path, *,
           on_progress: Callable[[float], bool] = lambda _pct: True, ffmpeg: str | None = None,
           preset: str = "veryfast", crf: int = 21, source_url: str | None = None) -> RenderResult:  # fmt: skip
    """Render ``spec`` to ``out_path``. ``on_progress(pct)`` is called every ~2 s; returning False aborts
    (RenderCancelled). Raises RenderError (VIDEO_UNREADABLE when nothing could be decoded, EXPORT_FAILED else)."""
    exe = ffmpeg or shutil.which("ffmpeg") or "ffmpeg"
    # Share the cores like the ONNX stages (entrypoint sets PITCHVISION_ORT_THREADS from --concurrency): x264 and
    # OpenCV otherwise each start one thread per core in every worker process.
    threads = default_intra_op_threads()
    if threads:
        cv2.setNumThreads(threads)
    f0, f1 = frame_range(spec)
    n = f1 - f0
    out_h = spec.out_height
    video_w = even(spec.width * out_h / spec.height)
    painter = Painter(spec, video_w, out_h)
    tracks = load_tracks(tracks_path, f0, f1)
    start_s, dur_s = f0 / spec.fps, n / spec.fps
    common = ["-nostdin", "-hide_banner", "-loglevel", "error"]
    url_in = ["-protocol_whitelist", "file,http,https,tcp,tls", "-ss", f"{start_s:.3f}", "-i", video_url]
    source = probe_source(source_url) if source_url and out_h > spec.height else None
    dec_in, from_source = _decoder_input(spec, video_url, source_url, source, start_s, dur_s, video_w, out_h)
    dec_cmd = [exe, *common, *dec_in, "-pix_fmt", "bgr24", "-f", "rawvideo", "-frames:v", str(n),
               "pipe:1"]  # fmt: skip
    enc_cmd = [exe, *common, "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{painter.width}x{out_h}",
               "-r", str(spec.fps), "-i", "pipe:0", "-t", f"{dur_s:.3f}", *url_in,
               "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
               *(["-threads", str(threads)] if threads else []),
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-shortest", "-movflags", "+faststart",
               str(out_path)]  # fmt: skip
    t_start = time.monotonic()
    dec = enc = None
    frames = 0
    try:
        limits = child_limits(FFMPEG_MAX_MEM_MB)  # the decoder may read the user's original upload
        dec = subprocess.Popen(dec_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=limits)
        enc = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=limits)
        assert dec.stdout is not None and enc.stdin is not None
        buf = bytearray(video_w * out_h * 3)
        view = memoryview(buf)
        frame = np.frombuffer(buf, np.uint8).reshape(out_h, video_w, 3)
        last = time.monotonic()
        for i in range(n):
            if not _read_exact(dec.stdout, view):
                break
            out = painter.paint(frame, f0 + i, tracks.rows(f0 + i))
            try:
                enc.stdin.write(out.tobytes() if out is not frame else buf)
            except BrokenPipeError:
                break
            frames += 1
            if time.monotonic() - last >= PROGRESS_EVERY_S:
                last = time.monotonic()
                if not on_progress(min(99.0, 100.0 * frames / n)):
                    raise RenderCancelled
        enc.stdin.close()
        dec.stdout.close()  # a decoder still writing (encoder died early) gets EPIPE instead of blocking forever
        dec_err = _stderr(dec, DECODER_WAIT_S)
        enc_err = _stderr(enc, ENCODER_WAIT_S)
    except BaseException:
        _kill(dec, enc)
        raise
    if frames == 0:
        raise RenderError("VIDEO_UNREADABLE", f"no frame decoded: {dec_err or 'empty range'}", retryable=True)
    if enc is None or enc.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        raise RenderError("EXPORT_FAILED", f"encoder failed: {enc_err}", retryable=True)
    if frames < n:
        log.info("export %s: %d of %d frames (video ends early)", spec.job_id, frames, n)
    return RenderResult(frames=frames, width=painter.width, height=out_h, seconds=time.monotonic() - t_start,
                        from_source=from_source)
