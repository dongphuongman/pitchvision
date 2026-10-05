"""FIFA pitch model (Laws of the Game, Law 1) with the 32 keypoints of the roboflow/sports convention.

Coordinates are **metres**, origin at the top-left corner flag as seen from the main broadcast camera:
``x`` runs along the touchline 0 → ``length`` (left goal line → right goal line), ``y`` runs across the pitch
0 → ``width`` (far touchline → near touchline, i.e. image-down for the main camera). Default 105 × 68 m.

Keypoint index (= YOLO-pose output channel order of the roboflow ``football-pitch-detection`` model and of our
fine-tune; SoccerNet calibration lines are converted to this order by ``training/keypoints/convert.py``):

====  ==========================  ======================  ============  ==================================
idx   name                        (x, y) m @105×68        kind          defined by
====  ==========================  ======================  ============  ==================================
 0    corner_tl                   (0, 0)                  intersection  left goal line × far touchline
 1    l_box_goal_far              (0, 13.84)              intersection  left goal line × penalty area side
 2    l_garea_goal_far            (0, 24.84)              intersection  left goal line × goal area side
 3    l_garea_goal_near           (0, 43.16)              intersection  left goal line × goal area side
 4    l_box_goal_near             (0, 54.16)              intersection  left goal line × penalty area side
 5    corner_bl                   (0, 68)                 intersection  left goal line × near touchline
 6    l_garea_front_far           (5.5, 24.84)            intersection  goal area front × side
 7    l_garea_front_near          (5.5, 43.16)            intersection  goal area front × side
 8    l_penalty_spot              (11, 34)                spot          painted spot (no line)
 9    l_box_front_far             (16.5, 13.84)           intersection  penalty area front × side
10    l_box_front_garea_far       (16.5, 24.84)           virtual       penalty area front at goal-area y
11    l_box_front_garea_near      (16.5, 43.16)           virtual       (no line crosses: extension only)
12    l_box_front_near            (16.5, 54.16)           intersection  penalty area front × side
13    half_far                    (52.5, 0)               intersection  halfway line × far touchline
14    half_circle_far             (52.5, 24.85)           arc           halfway line × centre circle
15    half_circle_near            (52.5, 43.15)           arc           halfway line × centre circle
16    half_near                   (52.5, 68)              intersection  halfway line × near touchline
17    r_box_front_far             (88.5, 13.84)           intersection  mirror of 9
18    r_box_front_garea_far       (88.5, 24.84)           virtual       mirror of 10
19    r_box_front_garea_near      (88.5, 43.16)           virtual       mirror of 11
20    r_box_front_near            (88.5, 54.16)           intersection  mirror of 12
21    r_penalty_spot              (94, 34)                spot          mirror of 8
22    r_garea_front_far           (99.5, 24.84)           intersection  mirror of 6
23    r_garea_front_near          (99.5, 43.16)           intersection  mirror of 7
24    corner_tr                   (105, 0)                intersection  mirror of 0
25    r_box_goal_far              (105, 13.84)            intersection  mirror of 1
26    r_garea_goal_far            (105, 24.84)            intersection  mirror of 2
27    r_garea_goal_near           (105, 43.16)            intersection  mirror of 3
28    r_box_goal_near             (105, 54.16)            intersection  mirror of 4
29    corner_br                   (105, 68)               intersection  mirror of 5
30    circle_left                 (43.35, 34)             arc           centre circle extreme (tangent pt)
31    circle_right                (61.65, 34)             arc           centre circle extreme (tangent pt)
====  ==========================  ======================  ============  ==================================

Kinds: ``intersection`` = two straight lines cross → precise; ``arc`` = on the centre circle (14/15 cross the
halfway line → precise; 30/31 are tangent extremes → only well defined along y, imprecise along x under
perspective, SP-03 cause 3); ``spot`` = painted penalty spot; ``virtual`` = a point on the penalty-area front line
at the goal-area y, not marked on the grass (annotators extrapolate it → noisier).

Only the markings scale with pitch size: Law 1 fixes the penalty / goal areas, circle and spots, while
length (100–110 m international) and width (64–75 m) vary per stadium. The roboflow model was trained against a
120 × 70 m template — mixing its box and touchline points into one homography is internally inconsistent (one
reason SP-03 overlays were mis-scaled). ``PitchTemplate(length, width)`` keeps the real size a parameter
(``pitch_prior.json`` from S3 may fit it later).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from typing import Literal

import numpy as np

KeypointKind = Literal["intersection", "arc", "spot", "virtual"]

# Law 1 markings (metres)
PENALTY_AREA_DEPTH = 16.5
PENALTY_AREA_WIDTH = 40.32
GOAL_AREA_DEPTH = 5.5
GOAL_AREA_WIDTH = 18.32
CENTRE_CIRCLE_R = 9.15
PENALTY_SPOT_DIST = 11.0
PENALTY_ARC_R = 9.15
CORNER_ARC_R = 1.0
GOAL_WIDTH = 7.32

NUM_KEYPOINTS = 32

KEYPOINT_NAMES: tuple[str, ...] = (
    "corner_tl", "l_box_goal_far", "l_garea_goal_far", "l_garea_goal_near", "l_box_goal_near", "corner_bl",
    "l_garea_front_far", "l_garea_front_near", "l_penalty_spot",
    "l_box_front_far", "l_box_front_garea_far", "l_box_front_garea_near", "l_box_front_near",
    "half_far", "half_circle_far", "half_circle_near", "half_near",
    "r_box_front_far", "r_box_front_garea_far", "r_box_front_garea_near", "r_box_front_near",
    "r_penalty_spot", "r_garea_front_far", "r_garea_front_near",
    "corner_tr", "r_box_goal_far", "r_garea_goal_far", "r_garea_goal_near", "r_box_goal_near", "corner_br",
    "circle_left", "circle_right",
)

KEYPOINT_KINDS: tuple[KeypointKind, ...] = (
    "intersection", "intersection", "intersection", "intersection", "intersection", "intersection",
    "intersection", "intersection", "spot",
    "intersection", "virtual", "virtual", "intersection",
    "intersection", "arc", "arc", "intersection",
    "intersection", "virtual", "virtual", "intersection",
    "spot", "intersection", "intersection",
    "intersection", "intersection", "intersection", "intersection", "intersection", "intersection",
    "arc", "arc",
)

# Left↔right mirror (x → length - x): horizontal-flip augmentation (``flip_idx`` in the YOLO dataset yaml).
FLIP_INDEX: tuple[int, ...] = (
    24, 25, 26, 27, 28, 29, 22, 23, 21, 17, 18, 19, 20, 13, 14, 15, 16,
    9, 10, 11, 12, 8, 6, 7, 0, 1, 2, 3, 4, 5, 31, 30,
)


@dataclass(frozen=True, slots=True)
class PitchLine:
    """One painted marking as a polyline in pitch metres (``closed`` = loop, e.g. the centre circle)."""

    name: str
    kind: Literal["segment", "arc", "spot"]
    points: np.ndarray  # float64 [N, 2]
    closed: bool = False


def _arc(cx: float, cy: float, r: float, a0: float, a1: float, step_m: float) -> np.ndarray:
    n = max(8, int(np.ceil(abs(a1 - a0) * r / step_m)) + 1)
    a = np.linspace(a0, a1, n)
    return np.c_[cx + r * np.cos(a), cy + r * np.sin(a)]


def _seg(p: tuple[float, float], q: tuple[float, float], step_m: float) -> np.ndarray:
    n = max(2, int(np.ceil(np.hypot(q[0] - p[0], q[1] - p[1]) / step_m)) + 1)
    return np.linspace(p, q, n)


@dataclass(frozen=True)
class PitchTemplate:
    length: float = 105.0
    width: float = 68.0
    _cache: dict[float, tuple[PitchLine, ...]] = field(default_factory=dict, repr=False, compare=False)

    @cached_property
    def keypoints(self) -> np.ndarray:
        """float64 [32, 2] metres, index order of the module docstring."""
        L, W = self.length, self.width
        pa_y0, pa_y1 = (W - PENALTY_AREA_WIDTH) / 2, (W + PENALTY_AREA_WIDTH) / 2
        ga_y0, ga_y1 = (W - GOAL_AREA_WIDTH) / 2, (W + GOAL_AREA_WIDTH) / 2
        c = W / 2
        left = [(0, 0), (0, pa_y0), (0, ga_y0), (0, ga_y1), (0, pa_y1), (0, W),
                (GOAL_AREA_DEPTH, ga_y0), (GOAL_AREA_DEPTH, ga_y1), (PENALTY_SPOT_DIST, c),
                (PENALTY_AREA_DEPTH, pa_y0), (PENALTY_AREA_DEPTH, ga_y0), (PENALTY_AREA_DEPTH, ga_y1),
                (PENALTY_AREA_DEPTH, pa_y1)]
        half = [(L / 2, 0), (L / 2, c - CENTRE_CIRCLE_R), (L / 2, c + CENTRE_CIRCLE_R), (L / 2, W)]
        right = [(L - PENALTY_AREA_DEPTH, pa_y0), (L - PENALTY_AREA_DEPTH, ga_y0), (L - PENALTY_AREA_DEPTH, ga_y1),
                 (L - PENALTY_AREA_DEPTH, pa_y1), (L - PENALTY_SPOT_DIST, c),
                 (L - GOAL_AREA_DEPTH, ga_y0), (L - GOAL_AREA_DEPTH, ga_y1),
                 (L, 0), (L, pa_y0), (L, ga_y0), (L, ga_y1), (L, pa_y1), (L, W)]
        circle = [(L / 2 - CENTRE_CIRCLE_R, c), (L / 2 + CENTRE_CIRCLE_R, c)]
        kp = np.array(left + half + right + circle, dtype=np.float64)
        assert kp.shape == (NUM_KEYPOINTS, 2)
        return kp

    def keypoint(self, name: str) -> np.ndarray:
        return self.keypoints[KEYPOINT_NAMES.index(name)]

    def lines(self, step_m: float = 0.5) -> tuple[PitchLine, ...]:
        """Every painted marking, sampled every ``step_m`` metres (straight segments: end points + samples)."""
        if step_m in self._cache:
            return self._cache[step_m]
        L, W, c = self.length, self.width, self.width / 2
        out: list[PitchLine] = []

        def seg(name: str, p: tuple[float, float], q: tuple[float, float]) -> None:
            out.append(PitchLine(name, "segment", _seg(p, q, step_m)))

        seg("touchline_far", (0, 0), (L, 0))
        seg("touchline_near", (0, W), (L, W))
        seg("goal_line_left", (0, 0), (0, W))
        seg("goal_line_right", (L, 0), (L, W))
        seg("halfway", (L / 2, 0), (L / 2, W))
        for side, x0, sgn in (("left", 0.0, 1.0), ("right", L, -1.0)):
            pa, ga = PENALTY_AREA_WIDTH / 2, GOAL_AREA_WIDTH / 2
            xp, xg = x0 + sgn * PENALTY_AREA_DEPTH, x0 + sgn * GOAL_AREA_DEPTH
            seg(f"penalty_area_{side}_far", (x0, c - pa), (xp, c - pa))
            seg(f"penalty_area_{side}_front", (xp, c - pa), (xp, c + pa))
            seg(f"penalty_area_{side}_near", (x0, c + pa), (xp, c + pa))
            seg(f"goal_area_{side}_far", (x0, c - ga), (xg, c - ga))
            seg(f"goal_area_{side}_front", (xg, c - ga), (xg, c + ga))
            seg(f"goal_area_{side}_near", (x0, c + ga), (xg, c + ga))
            # penalty arc: the part of the r=9.15 circle around the spot outside the penalty area
            xs = x0 + sgn * PENALTY_SPOT_DIST
            half_angle = float(np.arccos((PENALTY_AREA_DEPTH - PENALTY_SPOT_DIST) / PENALTY_ARC_R))
            a_mid = 0.0 if sgn > 0 else np.pi
            out.append(PitchLine(f"penalty_arc_{side}", "arc",
                                 _arc(xs, c, PENALTY_ARC_R, a_mid - half_angle, a_mid + half_angle, step_m)))
            out.append(PitchLine(f"penalty_spot_{side}", "spot", np.array([[xs, c]], dtype=np.float64)))
        out.append(PitchLine("centre_circle", "arc", _arc(L / 2, c, CENTRE_CIRCLE_R, 0, 2 * np.pi, step_m),
                             closed=True))
        out.append(PitchLine("centre_spot", "spot", np.array([[L / 2, c]], dtype=np.float64)))
        for name, (cx, cy), a0 in (("corner_arc_tl", (0, 0), 0.0), ("corner_arc_tr", (L, 0), np.pi / 2),
                                   ("corner_arc_br", (L, W), np.pi), ("corner_arc_bl", (0, W), 1.5 * np.pi)):
            out.append(PitchLine(name, "arc", _arc(cx, cy, CORNER_ARC_R, a0, a0 + np.pi / 2, step_m)))
        lines = tuple(out)
        self._cache[step_m] = lines
        return lines

    def contains(self, xy: np.ndarray, margin: float = 0.0) -> np.ndarray:
        """bool [N]: points inside the pitch rectangle grown by ``margin`` metres."""
        p = np.asarray(xy, float).reshape(-1, 2)
        return ((p[:, 0] >= -margin) & (p[:, 0] <= self.length + margin)
                & (p[:, 1] >= -margin) & (p[:, 1] <= self.width + margin))


FIFA_105x68 = PitchTemplate()
