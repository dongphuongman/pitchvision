"""S2 normalise (M1-CV-07): synthetic clips with the source frame index burned in (SP-08 style)."""
from __future__ import annotations

import math
import os
import shutil
import subprocess
import time
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest
from cv_core.io.normalize import (
    MediaError,
    MediaInfo,
    build_command,
    normalize,
    probe,
    target_size,
    validate_media,
)

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                                reason="ffmpeg/ffprobe not installed")

BITS = 12
BLOCK = 40  # px per bit in the 640×360 source


def make_clip(path: Path, fps: Fraction, seconds: float, size: tuple[int, int] = (640, 360),
              audio: bool = False) -> int:
    """Encode ``seconds`` of video where frame i shows i as 12 black/white blocks; returns the frame count."""
    w, h = size
    n = math.floor(seconds * fps)
    argv = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{w}x{h}",
            "-r", f"{fps.numerator}/{fps.denominator}", "-i", "-"]
    if audio:
        argv += ["-f", "lavfi", "-t", str(seconds), "-i", "sine=frequency=440:sample_rate=44100", "-c:a", "aac",
                 "-ac", "2"]
    argv += ["-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-pix_fmt", "yuv420p", "-g", "30", str(path)]
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE)
    assert proc.stdin is not None
    for i in range(n):
        img = np.full((h, w), 128, np.uint8)
        for b in range(BITS):
            img[0:BLOCK, b * BLOCK:(b + 1) * BLOCK] = 255 if (i >> b) & 1 else 0
        proc.stdin.write(img.tobytes())
    proc.stdin.close()
    assert proc.wait(timeout=120) == 0
    return n


def read_index(gray: np.ndarray, src_w: int = 640) -> int:
    s = gray.shape[1] / src_w
    y = int(BLOCK * s / 2)
    return sum(1 << b for b in range(BITS) if gray[y, int((b + 0.5) * BLOCK * s)] > 128)


def decode(path: Path) -> tuple[list[int], list[bool], list[float]]:
    """Burned index, keyframe flag and pts (s) of every frame of ``path``."""
    idx, key, pts = [], [], []
    with av.open(str(path)) as c:
        st = c.streams.video[0]
        for f in c.decode(st):
            idx.append(read_index(f.to_ndarray(format="gray")))
            key.append(bool(f.key_frame))
            pts.append(float(f.pts * st.time_base))
    return idx, key, pts


def nearest_src(k: int, src_fps: Fraction, out_fps: int = 25) -> int:
    return math.floor(Fraction(k, out_fps) * src_fps + Fraction(1, 2))


@pytest.mark.parametrize("src_fps", [Fraction(60), Fraction(30000, 1001), Fraction(50)])
def test_normalize_resamples_by_frame_selection(tmp_path: Path, src_fps: Fraction) -> None:
    src = tmp_path / "src.mp4"
    n_src = make_clip(src, src_fps, 6.0)
    res = normalize(src, tmp_path / "normalized.mp4", long_side=320, encoder="libx264", crf=10)
    out = res.output
    assert (out.width, out.height) == (320, 180)
    assert out.fps == 25 and out.fps_fraction == 25
    idx, key, pts = decode(res.path)
    # constant 25 fps from 0
    assert pts[0] == 0
    assert np.allclose(np.diff(pts), 0.04)
    # frame count: one per 1/25 s slot of the source duration
    assert abs(len(idx) - n_src / src_fps * 25) <= 1
    assert out.frame_count == len(idx)
    # frame k shows the source frame nearest k / 25 s
    expected = [nearest_src(k, src_fps) for k in range(len(idx))]
    assert idx == expected
    # GOP: keyframes exactly every 50 frames (2 s)
    assert [i for i, k in enumerate(key) if k] == list(range(0, len(idx), 50))
    m = res.manifest_fields()
    assert m == {"fps": 25, "width": 320, "height": 180, "duration_ms": out.duration_ms, "frame_count": len(idx)}
    assert abs(m["duration_ms"] - len(idx) * 40) <= 40


def test_normalize_keeps_aac_mono_audio_and_faststart(tmp_path: Path) -> None:
    src = tmp_path / "src.mp4"
    make_clip(src, Fraction(25), 3.0, audio=True)
    res = normalize(src, tmp_path / "n.mp4", encoder="libx264")
    assert (res.output.width, res.output.height) == (640, 360)  # never upscaled
    assert res.output.audio_codec == "aac" and res.output.audio_channels == 1
    head = res.path.read_bytes()[:4096]
    assert head.find(b"moov") != -1 and head.find(b"moov") < max(head.find(b"mdat"), 4096)
    assert not (tmp_path / "n.mp4.partial").exists()


def test_normalize_drops_audio_when_asked(tmp_path: Path) -> None:
    src = tmp_path / "src.mp4"
    make_clip(src, Fraction(25), 2.0, audio=True)
    res = normalize(src, tmp_path / "n.mp4", audio=False)
    assert res.output.audio_codec is None


def test_probe_rejects_non_video(tmp_path: Path) -> None:
    junk = tmp_path / "x.mp4"
    junk.write_bytes(b"not a video at all" * 100)
    with pytest.raises(MediaError) as e:
        probe(junk)
    assert e.value.code == "UNREADABLE_MEDIA"
    with pytest.raises(MediaError):
        probe(tmp_path / "missing.mp4")


def _info(**kw: object) -> MediaInfo:
    base: dict[str, object] = dict(container="mov,mp4,m4a,3gp,3g2,mj2", video_codec="h264", width=1920, height=1080,
                                   fps=50.0, fps_fraction=Fraction(50), duration_ms=90 * 60_000, frame_count=270_000)
    base.update(kw)
    return MediaInfo(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(("kw", "code"), [
    ({"container": "image2"}, "UNSUPPORTED_CONTAINER"),
    ({"video_codec": "gif"}, "UNSUPPORTED_CODEC"),
    ({"width": 16000, "height": 9000}, "BAD_DIMENSIONS"),
    ({"width": 100, "height": 60}, "BAD_DIMENSIONS"),
    ({"fps": 1000.0}, "BAD_FRAME_RATE"),
    ({"duration_ms": 30 * 3600_000}, "BAD_DURATION"),
    ({"duration_ms": 200}, "BAD_DURATION"),
])
def test_validate_rejects_absurd_inputs(kw: dict[str, object], code: str) -> None:
    with pytest.raises(MediaError) as e:
        validate_media(_info(**kw))
    assert e.value.code == code


def test_validate_accepts_broadcast() -> None:
    validate_media(_info())
    validate_media(_info(container="matroska,webm", video_codec="vp9", width=3840, height=2160))


@pytest.mark.parametrize(("w", "h", "ls", "out"), [
    (1920, 1080, 1280, (1280, 720)), (3840, 2160, 1280, (1280, 720)), (1440, 1080, 1280, (1280, 960)),
    (1080, 1920, 1280, (720, 1280)), (854, 480, 1280, (854, 480)), (1918, 1080, 1280, (1280, 720)),
    (853, 480, 1280, (852, 480)),
])
def test_target_size_even_no_upscale(w: int, h: int, ls: int, out: tuple[int, int]) -> None:
    assert target_size(w, h, ls) == out
    assert all(d % 2 == 0 for d in target_size(w, h, ls))


def test_command_is_hardened() -> None:
    argv = build_command(Path("in.mp4"), Path("out.mp4"), _info(audio_codec="aac"), fps=25, long_side=1280,
                         keyframe_interval_s=2, encoder="libx264", crf=21, preset=None, audio=True, threads=None)
    s = " ".join(argv)
    assert "-nostdin" in argv and "-protocol_whitelist file" in s  # stdin closed, local files only
    assert "-g 50 -keyint_min 50" in s and "-sc_threshold 0" in s
    assert "fps=fps=25:round=near" in s and "scale=1280:720" in s
    assert "+faststart" in s and "-ac 1" in s and "-b:a 96k" in s


GOLDEN = Path(os.environ.get("PITCHVISION_GOLDEN_DIR",
                             Path(__file__).resolve().parents[3] / "data" / "golden")) / "raw" / "BukNW8GRI5A.mp4"


@pytest.mark.skipif(not GOLDEN.is_file(), reason="golden video not available")
def test_golden_60s_normalise_speed(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "300", "-t", "60", "-i", str(GOLDEN), "-c", "copy",
                    str(clip)], check=True, timeout=120)
    t0 = time.perf_counter()
    res = normalize(clip, tmp_path / "normalized.mp4")
    dt = time.perf_counter() - t0
    print(f"\n60 s golden clip ({res.source.width}x{res.source.height}@{res.source.fps:.2f}) → "
          f"{res.output.width}x{res.output.height}@25 with {res.encoder}: {dt:.1f}s ({60 / dt:.1f}× realtime)")
    assert res.output.fps == 25 and max(res.output.width, res.output.height) == 1280
    assert abs(res.output.frame_count - 1500) <= 2
    assert dt < 60  # must be faster than realtime even in software
