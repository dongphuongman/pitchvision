"""M1-CV-02 — VideoReader frame-index exactness on synthetic H.264 clips with the source frame
index burned into the pixels as a barcode (SP-08 idea)."""
from __future__ import annotations

import itertools
import os
import time
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest
from cv_core.io import Frame, VideoReader, output_size, probe

W, H = 320, 180
BITS, CELL = 12, 24  # full-height bars, MSB first → survives H.264 + downscaling


def burn(idx: int) -> np.ndarray:
    img = np.zeros((H, W, 3), np.uint8)
    for b in range(BITS):
        if (idx >> (BITS - 1 - b)) & 1:
            img[:, b * CELL:(b + 1) * CELL] = 255
    img[:, BITS * CELL:] = (0, 128, 255)
    return img


def read_code(img: np.ndarray) -> int:
    h, w = img.shape[:2]
    cell = CELL * w / W
    row = img[h // 2, :, :].mean(axis=1)
    return sum(int(row[int((b + 0.5) * cell)] > 128) << (BITS - 1 - b) for b in range(BITS))


def make_clip(path: Path, fps: Fraction, n: int, gop: int) -> Path:
    with av.open(str(path), "w") as out:
        st = out.add_stream("libx264", rate=fps)
        st.width, st.height, st.pix_fmt = W, H, "yuv420p"
        st.options = {"crf": "12", "g": str(gop), "keyint_min": str(gop), "bf": "2", "sc_threshold": "0"}
        for i in range(n):
            fr = av.VideoFrame.from_ndarray(burn(i), format="bgr24")
            fr.pts = i
            for pkt in st.encode(fr):
                out.mux(pkt)
        for pkt in st.encode():
            out.mux(pkt)
    return path


@pytest.fixture(scope="session")
def clips(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("clips")
    return {
        "25": make_clip(d / "c25.mp4", Fraction(25), 250, 50),            # 10 s, keyframe 2 s (S2 output)
        "60": make_clip(d / "c60.mp4", Fraction(60), 480, 120),           # 8 s broadcast 60 fps
        "2997": make_clip(d / "c2997.mp4", Fraction(30000, 1001), 240, 60),
        "20": make_clip(d / "c20.mp4", Fraction(20), 120, 40),            # lower than target → duplicates
    }


def nearest_src(k: int, out_fps: Fraction, src_fps: Fraction, n_src: int) -> int:
    """Expected source frame for output slot k: nearest in time, ties → earlier."""
    t = Fraction(k) / out_fps
    return min(range(n_src), key=lambda n: (abs(Fraction(n) / src_fps - t), n))


def codes(frames: list[Frame]) -> list[int]:
    return [read_code(f.image) for f in frames]


def test_barcode_roundtrip() -> None:
    for i in (0, 1, 777, 4095):
        assert read_code(burn(i)) == i


def test_probe(clips: dict[str, Path]) -> None:
    info = probe(clips["25"])
    assert (info.width, info.height, info.codec) == (W, H, "h264")
    assert info.fps == 25 and info.duration_s == pytest.approx(10, abs=0.05)
    assert abs(probe(clips["2997"]).fps - 29.97) < 0.01


def test_full_read_indices_exact(clips: dict[str, Path]) -> None:
    frames = list(VideoReader(clips["25"]))
    assert [f.idx for f in frames] == list(range(250))
    assert codes(frames) == list(range(250))
    assert [f.pts_ms for f in frames] == [i * 40 for i in range(250)]
    assert frames[0].image.shape == (H, W, 3) and frames[0].image.dtype == np.uint8
    assert tuple(frames[0].image[H // 2, W - 1]) == pytest.approx((0, 128, 255), abs=12)  # BGR order


@pytest.mark.parametrize(("t0", "t1", "first", "end"), [
    (2.0, 4.0, 50, 100),     # keyframe-aligned
    (3.3, 5.02, 83, 126),    # mid-GOP; 82.5 and 125.5 round half up
    (0.0, 0.04, 0, 1),
    (9.9, None, 248, 250),   # tail
    (4.0, 4.0, 0, 0),        # empty
])
def test_range_half_open(clips: dict[str, Path], t0: float, t1: float | None, first: int, end: int) -> None:
    frames = list(VideoReader(clips["25"], t0, t1))
    assert [f.idx for f in frames] == list(range(first, end))
    assert codes(frames) == list(range(first, end))


def test_stride_globally_aligned(clips: dict[str, Path]) -> None:
    frames = list(VideoReader(clips["25"], 3.3, 6.0, stride=2))
    assert [f.idx for f in frames] == list(range(84, 150, 2))
    assert codes(frames) == [f.idx for f in frames]


@pytest.mark.parametrize("clip", ["25", "60", "2997"])
@pytest.mark.parametrize("stride", [1, 2])
def test_adjacent_ranges_no_gap_no_dup(clips: dict[str, Path], clip: str, stride: int) -> None:
    full = list(VideoReader(clips[clip], stride=stride, target_fps=25))
    cuts = [0.0, 1.3, 2.0, 4.66, 6.5, None]
    parts: list[Frame] = []
    for a, b in itertools.pairwise(cuts):
        parts += VideoReader(clips[clip], a, b, stride=stride, target_fps=25)
    assert [f.idx for f in parts] == [f.idx for f in full]
    assert codes(parts) == codes(full)
    assert len({f.idx for f in full}) == len(full)


@pytest.mark.parametrize(("clip", "src_fps", "n_src"), [
    ("60", Fraction(60), 480),
    ("2997", Fraction(30000, 1001), 240),
    ("20", Fraction(20), 120),
])
def test_resample_to_25_picks_nearest(clips: dict[str, Path], clip: str, src_fps: Fraction, n_src: int) -> None:
    r = VideoReader(clips[clip], target_fps=25)
    assert r.fps == 25 and r.resampling
    frames = list(r)
    last_t = Fraction(n_src - 1) / src_fps
    n_out = int(last_t * 25 + Fraction(1, 2)) + 1
    assert [f.idx for f in frames] == list(range(n_out))
    assert codes(frames) == [nearest_src(k, Fraction(25), src_fps, n_src) for k in range(n_out)]


def test_resample_range_mid_clip(clips: dict[str, Path]) -> None:
    frames = list(VideoReader(clips["60"], 2.5, 5.0, target_fps=25))
    assert [f.idx for f in frames] == list(range(63, 125))  # 2.5 s × 25 = 62.5 → half up = 63
    assert codes(frames) == [nearest_src(k, Fraction(25), Fraction(60), 480) for k in range(63, 125)]


def test_long_side_resize(clips: dict[str, Path]) -> None:
    assert output_size(1920, 1080, 1280) == (1280, 720)
    assert output_size(1440, 1080, 1280) == (1280, 960)
    frames = list(VideoReader(clips["25"], 1.0, 2.0, long_side=160))
    assert frames[0].image.shape == (90, 160, 3)
    assert codes(frames) == list(range(25, 50))


def test_batches(clips: dict[str, Path]) -> None:
    sizes = [len(b) for b in VideoReader(clips["25"], 0, 2.0).batches(16)]
    assert sizes == [16, 16, 16, 2]


@pytest.mark.parametrize("hw", ["auto", "cuda", "no-such-device", "videotoolbox"])
def test_hwaccel_is_optional(clips: dict[str, Path], hw: str) -> None:
    r = VideoReader(clips["25"], 1.0, 2.0, hwaccel=hw)
    frames = list(r)
    assert isinstance(r.hw_active, bool)
    assert codes(frames) == list(range(25, 50))


def test_invalid_args(clips: dict[str, Path]) -> None:
    with pytest.raises(ValueError):
        VideoReader(clips["25"], stride=0)
    with pytest.raises(ValueError):
        VideoReader(clips["25"], 5, 4)


# --- optional: a real golden broadcast (gitignored, never in the repo) -------------------------

GOLDEN = Path(os.environ.get("PITCHVISION_GOLDEN_DIR", Path(__file__).resolve().parents[3] / "data" / "golden"))
GOLDEN_CLIP = GOLDEN / "raw" / "BukNW8GRI5A.mp4"  # MLS, 59.94 fps 1080p


@pytest.mark.skipif(not GOLDEN_CLIP.is_file(), reason="golden videos not present (set PITCHVISION_GOLDEN_DIR)")
def test_golden_decode_sanity() -> None:
    info = probe(GOLDEN_CLIP)
    assert info.fps == pytest.approx(59.94, abs=0.01) and (info.width, info.height) == (1920, 1080)
    r = VideoReader(GOLDEN_CLIP, 100.0, 104.0, target_fps=25, long_side=1280)
    t = time.perf_counter()
    frames = list(r)
    dt = time.perf_counter() - t
    assert [f.idx for f in frames] == list(range(2500, 2600))
    assert frames[0].image.shape == (720, 1280, 3)
    assert all(abs(f.pts_ms - f.idx * 40) <= 9 for f in frames)  # nearest 59.94 fps frame: ≤ half a period
    assert len(frames) / dt > 5, f"decode too slow: {len(frames) / dt:.1f} fps"
