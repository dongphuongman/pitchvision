"""VideoReader — sequential PyAV decode on the normalised timeline (AD §6.2 S2/S4).

Rules learnt in the spikes:
- never seek per frame (OpenCV per-frame seeking corrupted tracking): seek ONCE to the keyframe
  before ``t0`` and decode forward, dropping frames before ``t0``;
- ``idx = round_half_up(pts_s * fps)`` with ``pts_s`` relative to the stream start and ``fps`` the
  output timeline rate (``target_fps`` or the source rate). The browser uses ``round(mediaTime × 25)``
  (SP-08), so both sides agree on frame numbers;
- ranges are half-open in index space: ``[round(t0·fps), round(t1·fps))``, so adjacent segments
  ``[a, b)`` + ``[b, c)`` never skip or duplicate a frame;
- ``stride`` keeps ``idx % stride == 0`` (globally aligned, so segments agree in Fast mode).

Images are **BGR uint8, HxWx3** (OpenCV convention, matches the spikes and cv2 drawing/colour code).
"""
from __future__ import annotations

import logging
import math
import os
from collections.abc import Iterator
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

import av
import numpy as np

log = logging.getLogger(__name__)

SEEK_MARGIN_S = 0.5  # seek a little before t0 so the nearest-frame choice for slot i0 sees every candidate

Source = str | os.PathLike[str]


@dataclass(frozen=True, slots=True)
class VideoInfo:
    fps: float
    width: int
    height: int
    duration_s: float
    codec: str
    frames: int | None  # container frame count when the header has it
    start_s: float      # stream start time; the timeline's 0


@dataclass(frozen=True, slots=True)
class Frame:
    idx: int            # frame index on the output timeline
    pts_ms: int         # presentation time of the decoded source frame, ms from stream start
    image: np.ndarray   # BGR uint8 HxWx3


def _round_half_up(x: Fraction) -> int:
    return math.floor(x + Fraction(1, 2))


def _as_fraction(fps: float | Fraction) -> Fraction:
    return fps if isinstance(fps, Fraction) else Fraction(fps).limit_denominator(1001)


def _stream_fps(stream: Any) -> Fraction:
    rate = stream.average_rate or stream.guessed_rate or stream.base_rate
    if not rate:
        raise ValueError("cannot determine the video frame rate")
    return Fraction(rate)


def output_size(width: int, height: int, long_side: int | None) -> tuple[int, int]:
    """(w, h) scaled so max(w, h) == long_side, rounded to even (as ffmpeg ``scale=-2``)."""
    if long_side is None or max(width, height) == long_side:
        return width, height
    s = long_side / max(width, height)
    return max(2, round(width * s / 2) * 2), max(2, round(height * s / 2) * 2)


def probe(src: Source) -> VideoInfo:
    """fps / size / duration / codec of the first video stream."""
    with av.open(str(src)) as c:
        st = c.streams.video[0]
        tb = st.time_base
        start = float(st.start_time * tb) if st.start_time is not None and tb else 0.0
        dur = float(st.duration * tb) if st.duration is not None and tb else (c.duration or 0) / av.time_base
        return VideoInfo(
            fps=float(_stream_fps(st)), width=st.codec_context.width, height=st.codec_context.height,
            duration_s=dur, codec=st.codec_context.name, frames=st.frames or None, start_s=start,
        )


def _resolve_hwaccel(hwaccel: str | None) -> str | None:
    if hwaccel in (None, "", "none"):
        return None
    try:
        from av.codec.hwaccel import hwdevices_available
    except ImportError:  # PyAV built without hwaccel support
        return None
    available = set(hwdevices_available())
    if hwaccel == "auto":
        return "cuda" if "cuda" in available else None
    if hwaccel not in available:
        log.warning("hwaccel %r not available (have %s); decoding in software", hwaccel, sorted(available))
        return None
    return hwaccel


class VideoReader:
    """Iterate ``Frame``s of ``src`` (path or URL) over ``[t0_s, t1_s)``.

    ``target_fps`` resamples by pts (nearest source frame per output slot; 50/60 → 25 drops, lower
    rates duplicate). ``long_side`` resizes (area). ``hwaccel``: None = software, ``"auto"`` = NVDEC
    when FFmpeg has CUDA, or a device type (``"cuda"``, ``"videotoolbox"``); always falls back to
    software, never required.
    """

    def __init__(self, src: Source, t0_s: float | None = None, t1_s: float | None = None, *, stride: int = 1,
                 target_fps: float | None = None, long_side: int | None = None, hwaccel: str | None = None) -> None:
        if stride < 1:
            raise ValueError("stride must be >= 1")
        if t0_s is not None and t1_s is not None and t1_s < t0_s:
            raise ValueError("t1_s must be >= t0_s")
        self.src = str(src)
        self.info = probe(src)
        self.stride = stride
        self.long_side = long_side
        self.hwaccel = _resolve_hwaccel(hwaccel)
        self.hw_active = False  # set once decoding starts
        self._src_fps = _as_fraction(self.info.fps)
        self._fps = _as_fraction(target_fps) if target_fps else None
        self.size = output_size(self.info.width, self.info.height, long_side)
        f = self.fps_fraction
        self.i0 = _round_half_up(Fraction(str(t0_s)) * f) if t0_s else 0
        self.i1 = _round_half_up(Fraction(str(t1_s)) * f) if t1_s is not None else None

    @property
    def fps_fraction(self) -> Fraction:
        return self._fps or self._src_fps

    @property
    def fps(self) -> float:
        """Output timeline rate (frames of index space per second, before stride)."""
        return float(self.fps_fraction)

    @property
    def resampling(self) -> bool:
        return self._fps is not None and self._fps != self._src_fps

    def _open(self) -> Any:
        if self.hwaccel:
            try:
                from av.codec.hwaccel import HWAccel

                c = av.open(self.src, hwaccel=HWAccel(device_type=self.hwaccel, allow_software_fallback=True))
                self.hw_active = bool(getattr(c.streams.video[0].codec_context, "is_hwaccel", False))
                return c
            except Exception as e:  # any hw init failure → software
                log.warning("hardware decode (%s) failed: %s; decoding in software", self.hwaccel, e)
        self.hw_active = False
        return av.open(self.src)

    def _to_image(self, frame: Any) -> np.ndarray:
        w, h = self.size
        if (w, h) == (frame.width, frame.height):
            return frame.to_ndarray(format="bgr24")
        return frame.reformat(width=w, height=h, format="bgr24", interpolation="AREA").to_ndarray()

    def __iter__(self) -> Iterator[Frame]:
        F, i0, i1 = self.fps_fraction, self.i0, self.i1
        if i1 is not None and i1 <= i0:
            return
        with self._open() as c:
            st = c.streams.video[0]
            st.thread_type = "AUTO"
            tb = st.time_base
            start = Fraction(st.start_time) * tb if st.start_time is not None else Fraction(0)
            if i0 > 0:
                seek_s = max(Fraction(0), Fraction(i0) / F - Fraction(SEEK_MARGIN_S))
                c.seek(int((start + seek_s) / tb), stream=st, backward=True, any_frame=False)
            yield from self._slots(c.decode(st), tb, start)

    def _slots(self, frames: Iterator[Any], tb: Fraction, start: Fraction) -> Iterator[Frame]:
        """Assign decoded frames to timeline slots: nearest frame per slot, gaps filled when resampling."""
        F, i0, i1 = self.fps_fraction, self.i0, self.i1
        fill_gaps = self.resampling
        last_k: int | None = None                                   # last slot handled
        last: tuple[Fraction, Any] | None = None                    # (pts_s, frame) of that slot
        pending: tuple[int, Fraction, Fraction, Any] | None = None  # (slot, dist, pts_s, frame)
        conv: list[tuple[Any, np.ndarray]] = []                     # tiny cache: duplicates reuse the image

        def frame_out(idx: int, p: Fraction, frame: Any) -> Frame:
            img = next((im for f, im in conv if f is frame), None)
            if img is None:
                img = self._to_image(frame)
                conv[:] = [*conv[-1:], (frame, img)]
            return Frame(idx=idx, pts_ms=_round_half_up(p * 1000), image=img)

        def emit(k: int, p: Fraction, frame: Any) -> Iterator[Frame]:
            nonlocal last_k, last
            if fill_gaps and last is not None and last_k is not None:
                for g in range(max(last_k + 1, i0), k if i1 is None else min(k, i1)):
                    gt = Fraction(g) / F
                    src_p, src_f = last if abs(last[0] - gt) <= abs(p - gt) else (p, frame)
                    if g % self.stride == 0:
                        yield frame_out(g, src_p, src_f)
            if i0 <= k and (i1 is None or k < i1) and k % self.stride == 0:
                yield frame_out(k, p, frame)
            last_k, last = k, (p, frame)

        for frame in frames:
            if frame.pts is None:
                continue
            p = Fraction(frame.pts) * tb - start
            k = _round_half_up(p * F)
            if last_k is not None and k <= last_k:
                continue  # slot already out (only with non-monotonic pts)
            if pending is not None and k != pending[0]:
                yield from emit(pending[0], pending[2], pending[3])
                pending = None
            if i1 is not None and k >= i1:
                yield from emit(k, p, frame)  # only fills gaps before i1
                return
            dist = abs(p - Fraction(k) / F)
            if pending is None or dist < pending[1]:
                pending = (k, dist, p, frame)
        if pending is not None:
            yield from emit(pending[0], pending[2], pending[3])

    def batches(self, n: int) -> Iterator[list[Frame]]:
        """Frames grouped in lists of ``n`` (last one may be shorter)."""
        buf: list[Frame] = []
        for f in self:
            buf.append(f)
            if len(buf) == n:
                yield buf
                buf = []
        if buf:
            yield buf
