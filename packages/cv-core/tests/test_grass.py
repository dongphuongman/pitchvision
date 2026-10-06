"""Pitch (grass) mask + bottom-centre on-pitch test."""
from __future__ import annotations

import numpy as np
from cv_core.pitch import grass_mask


def test_grass_mask_and_on_pitch() -> None:
    img = np.zeros((720, 1280, 3), np.uint8)
    img[:] = (90, 90, 90)                    # stands
    img[200:, :] = (40, 150, 40)             # pitch below y = 200
    img[400:450, 600:620] = (40, 40, 220)    # a player inside the pitch (closed into the mask)
    img[20:120, 1000:1200] = (40, 150, 40)   # a small green ad board in the stands: not the largest blob
    m = grass_mask(img)
    assert 0.6 < m.coverage < 0.8
    boxes = np.array([[600, 400, 620, 450],     # player feet on grass
                      [100, 100, 120, 150],     # spectator in the stands
                      [1050, 60, 1070, 110],    # feet on the ad board
                      [0, 650, 20, 900]])       # feet below the image
    np.testing.assert_array_equal(m.on_pitch(boxes), [True, False, False, False])
    assert m.on_pitch(np.zeros((0, 4))).shape == (0,)
