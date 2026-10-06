"""Team display colours from torso crops (S3 → teams.color / manifest teams)."""
import numpy as np
from cv_core.teams import cluster_colours
from cv_core.teams.colors import crop_mean_bgr, to_hex

GRASS = (40, 150, 40)


def crop(bgr: tuple[int, int, int], grass_cols: int = 4) -> np.ndarray:
    img = np.zeros((20, 12, 3), np.uint8)
    img[:] = bgr
    img[:, :grass_cols] = GRASS  # slim player: grass on the side
    return img


def test_cluster_colours_ignore_grass_and_outliers() -> None:
    red, blue = (30, 30, 200), (200, 60, 30)
    crops = [crop(red), crop((40, 40, 210)), crop(blue), crop(blue), crop((255, 255, 255))]
    out = cluster_colours(crops, [0, 0, 1, 1, -1])
    assert out[1] == to_hex(np.array(blue)) == "#1e3cc8"
    assert out[0] == "#cd2323"  # median of the two reds (200 / 210 → 205), grass excluded
    assert cluster_colours([crop(red)], [0]) == {0: "#c81e1e"}
    assert crop_mean_bgr(np.zeros((0, 0, 3), np.uint8)) is None
    assert crop_mean_bgr(np.full((10, 10, 3), GRASS, np.uint8)) is None  # all grass
