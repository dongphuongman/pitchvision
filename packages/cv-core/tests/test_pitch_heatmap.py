"""HeatmapPitchModel (heatmap-world/v1): peak decoding, scaling to source px, confidence rescale, world table."""

import numpy as np
from cv_core.pitch import HeatmapPitchModel, estimate_homography
from cv_core.pitch.heatmap import heatmap_peaks, rescale_conf
from cv_core.runtime.model import TensorInfo


class FakeModel:
    name, provider = "fake", "cpu"

    def __init__(self, hm: np.ndarray) -> None:
        self.hm = hm
        self.inputs = (TensorInfo("image", (1, 3, 540, 960), np.dtype(np.float32)),)
        self.outputs = (TensorInfo("heatmaps", (1, hm.shape[0], 270, 480), np.dtype(np.float32)),)
        self.seen: list[tuple[int, ...]] = []

    def run(self, feeds: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        self.seen.append(feeds["image"].shape)
        return {"heatmaps": self.hm[None]}


def test_peaks_and_conf() -> None:
    hm = np.zeros((2, 10, 20), np.float32)
    hm[0, 3, 7] = 0.9
    hm[1, 0, 5] = 0.95  # on the border: ignored
    xy, s = heatmap_peaks(hm)
    assert xy[0].tolist() == [7, 3] and s.tolist() == [np.float32(0.9), 0.0]
    assert rescale_conf(np.array([0.0, 0.3434, 1.0]), 0.3434).round(3).tolist() == [0.0, 0.5, 1.0]


def test_model_maps_peaks_to_source_pixels_and_fits() -> None:
    # 5 ground points of a fronto-parallel 10 px / m view (source 1280×720) + one crossbar point (null world)
    world = [[0, 0], [52.5, 0], [52.5, 34], [16.5, 13.84], [16.5, 54.16], None]
    src = [(100, 50), (625, 50), (625, 390), (265, 188.4), (265, 591.6), (300, 300)]
    hm = np.zeros((6, 270, 480), np.float32)
    for k, (x, y) in enumerate(src):
        hm[k, round(y * 270 / 720), round(x * 480 / 1280)] = 0.8
    fake = FakeModel(hm)
    kp = HeatmapPitchModel(fake, world=world, threshold=0.3434)([np.zeros((720, 1280, 3), np.uint8)])[0]
    assert fake.seen == [(1, 3, 540, 960)]
    assert np.allclose(kp.xy, src, atol=2.7) and kp.conf.min() > 0.5
    assert np.isnan(kp.world[5]).all()
    est = estimate_homography(kp)
    assert est.ok, est.reason
    assert not est.used[5]  # the crossbar point never enters the fit
    centre = est.H @ np.array([625, 390, 1.0])
    assert np.allclose(centre[:2] / centre[2], [52.5, 34], atol=0.5)
