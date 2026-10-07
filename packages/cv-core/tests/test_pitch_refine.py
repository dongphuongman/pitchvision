"""M1-CV-20: homography refinement by line alignment (cv_core.pitch.refine)."""
import numpy as np
import pytest
from cv_core.pitch import line_mask, project
from cv_core.pitch.refine import refine_homography
from pitch_synth import camera_homography, perturb, render_frame


def _err_px(H_a: np.ndarray, H_b: np.ndarray) -> float:
    """Mean image distance between the two projections of the centre-half of the pitch (what a viewer sees)."""
    g = np.stack(np.meshgrid(np.linspace(20, 85, 14), np.linspace(5, 63, 12)), -1).reshape(-1, 2)
    return float(np.linalg.norm(project(H_a, g) - project(H_b, g), axis=1).mean())


@pytest.mark.parametrize(
    ("target", "wrong"),
    [
        ((52.5, 34.0), {"shift_m": (1.2, -0.8)}),
        ((30.0, 30.0), {"rot_deg": 1.5}),
        ((80.0, 36.0), {"scale": 1.03, "shift_m": (0.6, 0.0)}),
        ((15.0, 34.0), {"shift_m": (-1.0, 0.5), "rot_deg": -0.8}),
    ],
)
def test_refinement_pulls_a_wrong_h_onto_the_lines(target: tuple[float, float], wrong: dict) -> None:
    H_true = camera_homography(target=target)
    frame = render_frame(H_true, seed=1, clutter=6)
    H_bad = perturb(H_true, centre=target, **wrong)
    mask = line_mask(frame)
    r = refine_homography(mask, H_bad)
    assert r.accepted and r.quality > r.quality_before
    before, after = _err_px(H_bad, H_true), _err_px(r.H_p2i, H_true)
    assert after < 0.3 * before and after < 3.0, (before, after)


def test_a_correct_h_stays_put() -> None:
    H_true = camera_homography(target=(40.0, 30.0))
    r = refine_homography(line_mask(render_frame(H_true, seed=2)), H_true)
    assert _err_px(r.H_p2i, H_true) < 1.5


def test_no_lines_no_change() -> None:
    H_true = camera_homography()
    frame = np.full((720, 1280, 3), (40, 130, 45), np.uint8)  # bare grass
    r = refine_homography(line_mask(frame), perturb(H_true, shift_m=(2.0, 0.0)))
    assert not r.accepted and np.array_equal(r.H_p2i, perturb(H_true, shift_m=(2.0, 0.0)))


def test_non_finite_input_is_returned() -> None:
    frame = render_frame(camera_homography())
    bad = np.full((3, 3), np.nan)
    r = refine_homography(line_mask(frame), bad)
    assert not r.accepted and r.quality == 0.0
