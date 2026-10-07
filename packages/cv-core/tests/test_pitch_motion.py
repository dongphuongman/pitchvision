"""Camera motion between frames and homography propagation from keyframes (cv_core.pitch.motion)."""

import cv2
import numpy as np
from cv_core.pitch.motion import CameraMotion, propagate


def _texture(seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    big = cv2.resize(rng.integers(0, 255, (90, 160), np.uint8), (1600, 900), interpolation=cv2.INTER_NEAREST)
    return cv2.cvtColor(cv2.GaussianBlur(big, (5, 5), 0), cv2.COLOR_GRAY2BGR)


def test_camera_motion_recovers_a_pan() -> None:
    scene, m = _texture(), CameraMotion()
    frames = [scene[100:820, 100 + 6 * k: 1380 + 6 * k] for k in range(5)]  # camera pans 6 px / frame to the right
    assert m.step(frames[0]) is None
    for f in frames[1:]:
        G = m.step(f)
        assert G is not None
        p = G @ np.array([640.0, 360.0, 1.0])
        assert np.allclose(p[:2] / p[2], [646.0, 360.0], atol=0.6)  # a pixel now was 6 px further right before
    m.reset()
    assert m.step(frames[0]) is None


def test_propagate_chains_and_blends_between_keyframes() -> None:
    n = 9
    T = lambda dx: np.array([[1, 0, dx], [0, 1, 0], [0, 0, 1.0]])  # noqa: E731
    true = [np.diag([0.1, 0.1, 1.0]) @ T(6 * k) for k in range(n)]  # image k → pitch (10 px per metre)
    motion = [None] + [T(6.0)] * (n - 1)  # frame k px → frame k-1 px
    Hs, qs = propagate(n, {0: (true[0], 0.9), 8: (true[8], 0.8)}, motion, set(), image_size=(1280, 720))
    for k in range(n):
        assert Hs[k] is not None and np.allclose(Hs[k], true[k], atol=1e-6)
    assert qs[0] == np.float32(0.9) and 0.75 < qs[4] < 0.9
    # a cut at 5 stops both chains there; a broken motion leaves frames unreached
    Hs, _ = propagate(n, {0: (true[0], 0.9)}, motion, {5}, image_size=(1280, 720))
    assert all(Hs[k] is not None for k in range(5)) and all(Hs[k] is None for k in range(5, n))
    broken = list(motion)
    broken[3] = None
    Hs, _ = propagate(n, {0: (true[0], 0.9)}, broken, set(), image_size=(1280, 720))
    assert Hs[2] is not None and Hs[3] is None
