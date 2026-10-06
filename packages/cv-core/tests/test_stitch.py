"""S5 stitch v1 (M1-CV-13) on synthetic segments: linking rules, global ids, interpolation, projection, IO."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

import numpy as np
import pytest
from cv_core.io.parquet import (
    CLS_BALL,
    CLS_PLAYER,
    CLS_REFEREE,
    NO_TRACK,
    TRACK_COLUMNS,
    read_homography,
    read_tracks,
    tracks_table,
    write_homography,
    write_tracks,
)
from cv_core.track import FrameHomographies, SegmentDetections, stitch

FPS = 25
Box = Callable[[int], tuple[float, float, float, float]]


def still(x: float, y: float = 300.0) -> Box:
    return lambda f: (x, y, x + 20, y + 50)


def moving(x0: float, vx: float, y: float = 300.0) -> Box:
    return lambda f: (x0 + vx * f, y, x0 + vx * f + 20, y + 50)


def seg(index: int, t0_ms: int, t1_ms: int, tracks: dict[int, tuple[Iterable[int], Box, int]] | None = None,
        *, ball: Iterable[int] = (), cls: dict[int, int] | None = None) -> SegmentDetections:
    rows: dict[str, list[float]] = {c: [] for c in ("frame_idx", "track_id", "cls", "team", "x1", "y1", "x2", "y2",
                                                   "conf")}
    for local, (frames, box, team) in (tracks or {}).items():
        for f in frames:
            for c, v in zip(("x1", "y1", "x2", "y2"), box(f), strict=True):
                rows[c].append(v)
            rows["frame_idx"].append(f)
            rows["track_id"].append(local)
            rows["cls"].append((cls or {}).get(local, CLS_PLAYER))
            rows["team"].append(team)
            rows["conf"].append(0.9)
    for f in ball:
        for c, v in zip(("x1", "y1", "x2", "y2"), (600, 400, 606, 406), strict=True):
            rows[c].append(v)
        rows["frame_idx"].append(f)
        rows["track_id"].append(NO_TRACK)
        rows["cls"].append(CLS_BALL)
        rows["team"].append(-1)
        rows["conf"].append(0.5)
    cols = {c: np.asarray(v) for c, v in rows.items()}
    return SegmentDetections(index, t0_ms, t1_ms, cols)


def frames(a_ms: int, b_ms: int) -> range:
    return range(a_ms * FPS // 1000, b_ms * FPS // 1000)


def ids_at(cols: dict[str, np.ndarray], frame: int) -> list[int]:
    return sorted(int(t) for t in cols["track_id"][cols["frame_idx"] == frame])


def test_links_across_keyframe_boundary_in_one_shot() -> None:
    a = seg(0, 0, 4000, {1: (frames(0, 4000), moving(100, 2), 0), 2: (frames(0, 2000), still(800), 1)},
            ball=frames(0, 4000))
    b = seg(1, 4000, 8000, {5: (frames(4000, 8000), moving(100, 2), 0), 6: (frames(4000, 8000), still(400), 1)},
            ball=frames(4000, 8000))
    res = stitch([b, a])  # input order does not matter
    c = res.columns
    assert res.links == [(0, 1, 1, 5)]
    assert res.stats["tracks"] == 3 and res.stats["links"] == 1
    assert ids_at(c, 99) == [-1, 1] and ids_at(c, 100) == [-1, 1, 3]  # 1 continues; 6 is new → 3
    assert ids_at(c, 10) == [-1, 1, 2]
    assert (c["track_id"] == NO_TRACK).sum() == 200 and set(c["cls"][c["track_id"] == NO_TRACK]) == {CLS_BALL}
    # sorted by (frame, track) and ts on the 25 fps grid
    key = c["frame_idx"] * 1000 + c["track_id"]
    assert np.all(np.diff(key) > 0)
    assert np.array_equal(c["ts_ms"], c["frame_idx"] * 40)
    assert not c["interpolated"].any() and np.isnan(c["px"]).all() and np.isnan(c["h_quality"]).all()


def test_no_link_across_a_shot_cut_or_gap() -> None:
    a = seg(0, 0, 4000, {1: (frames(0, 4000), still(100), 0)})
    b = seg(1, 4000, 8000, {1: (frames(4000, 8000), still(100), 0)})
    cut = stitch([a, b], cut_frames=[100])
    assert cut.links == [] and cut.stats["tracks"] == 2 and cut.stats["boundaries_on_cuts"] == 1
    c = seg(1, 6000, 9000, {1: (frames(6000, 9000), still(100), 0)})  # non-wide footage in between
    assert stitch([a, c]).links == []
    assert len(stitch([a, b], cut_frames=[50]).links) == 1  # a cut elsewhere does not matter


def test_link_constraints_team_role_iou_gap() -> None:
    a = seg(0, 0, 2000, {1: (frames(0, 2000), still(100), 0), 2: (frames(0, 2000), still(300), -1),
                         3: (frames(0, 2000), still(500), 0), 4: (frames(0, 1400), still(700), 1),
                         7: (frames(0, 2000), still(900), -1)}, cls={7: CLS_REFEREE})
    b = seg(1, 2000, 4000, {1: (frames(2000, 4000), still(100), 1),       # other team → new
                            2: (frames(2000, 4000), still(300), 1),       # -1 ↔ 1 compatible → linked
                            3: (frames(2000, 4000), still(560), 0),       # moved 60 px: IoU 0 → new
                            4: (frames(2000, 4000), still(700), 1),       # resumes 0.6 s later → linked
                            7: (frames(2000, 4000), still(900), -1)})     # player vs referee → new
    res = stitch([a, b])
    assert sorted((la, lb) for _, la, _, lb in res.links) == [(2, 2), (4, 4)]
    c = res.columns
    # majority team over the linked track's rows: 50 rows team -1, 50 rows team 1 → 1
    t2 = c["track_id"][(c["frame_idx"] == 10) & (c["x1"] == 300)][0]
    assert set(c["team"][c["track_id"] == t2]) == {1}
    t4 = c["track_id"][(c["frame_idx"] == 10) & (c["x1"] == 700)][0]
    assert set(c["frame_idx"][c["track_id"] == t4]) == set(range(0, 35)) | set(range(50, 100))  # 0.6 s gap kept
    ref = c["track_id"][(c["frame_idx"] == 60) & (c["x1"] == 900)][0]
    assert set(c["cls"][c["track_id"] == ref]) == {CLS_PLAYER}  # b's local 7 is a player row


def test_ambiguous_candidates_take_the_best_iou() -> None:
    a = seg(0, 0, 2000, {1: (frames(0, 2000), still(100), 0)})
    b = seg(1, 2000, 4000, {1: (frames(2000, 4000), still(108), 0), 2: (frames(2000, 4000), still(102), 0)})
    assert [lb for *_, lb in stitch([a, b]).links] == [2]


def test_short_gaps_interpolated_long_gaps_kept() -> None:
    f = [*range(0, 20), *range(25, 40), *range(52, 60)]  # 5 missing frames (0.2 s), then 12 (0.48 s)
    res = stitch([seg(0, 0, 4000, {1: (f, moving(100, 4), 0)})])
    c = res.columns
    assert list(c["frame_idx"][c["interpolated"]]) == [20, 21, 22, 23, 24]
    assert res.stats["interpolated_rows"] == 5
    assert 45 not in set(c["frame_idx"])
    i22 = np.flatnonzero(c["frame_idx"] == 22)[0]
    assert c["x1"][i22] == pytest.approx(100 + 4 * 22)  # linear in time
    stride2 = stitch([seg(0, 0, 4000, {1: (range(0, 100, 2), still(100), 0)})])
    assert stride2.stats["rows"] == 99 and stride2.columns["interpolated"].sum() == 49


def test_projection_with_frame_homographies() -> None:
    # image→pitch: 10 px per metre, origin at (100, 100)
    H = np.array([[0.1, 0, -10], [0, 0.1, -10], [0, 0, 1]], float)
    hom = FrameHomographies(np.arange(0, 50), np.repeat(H[None], 50, axis=0), np.full(50, 0.8, np.float32))
    hom = FrameHomographies(hom.frame_idx, hom.H.copy(), hom.quality)
    hom.H[10] = np.nan  # frame 10: smoother had nothing
    res = stitch([seg(0, 0, 4000, {1: (range(0, 60), lambda f: (290, 550, 310, 600), 0),
                                   2: (range(0, 60), lambda f: (5000, 550, 5020, 600), 1)})], homography=hom)
    c = res.columns
    one = c["track_id"] == 1
    assert c["px"][one & (c["frame_idx"] == 0)][0] == pytest.approx(20.0)   # bottom-centre (300, 600)
    assert c["py"][one & (c["frame_idx"] == 0)][0] == pytest.approx(50.0)
    f10 = c["frame_idx"] == 10
    assert np.isnan(c["px"][one & f10]).all() and np.isnan(c["h_quality"][f10]).all()
    assert np.isnan(c["px"][one & (c["frame_idx"] >= 50)]).all()            # past the homography range
    assert c["h_quality"][one & (c["frame_idx"] == 3)][0] == pytest.approx(0.8)
    assert np.isnan(c["px"][c["track_id"] == 2]).all()                       # 490 m off the pitch
    assert res.stats["projected_rows"] == 49


def test_deterministic_and_empty() -> None:
    a = seg(0, 0, 2000, {3: (frames(0, 2000), still(100), 0), 9: (frames(0, 1000), still(400), 1)})
    b = seg(1, 2000, 4000, {2: (frames(2000, 4000), still(100), 0)})
    r1, r2 = stitch([a, b]), stitch([b, a])
    for k in r1.columns:
        assert np.array_equal(r1.columns[k], r2.columns[k], equal_nan=True)
    assert sorted(set(r1.columns["track_id"].tolist())) == [1, 2]  # ids from 1, in (segment, local) order
    empty = stitch([seg(0, 0, 1000)])
    assert empty.stats["rows"] == 0 and empty.stats["tracks"] == 0


def test_tracks_and_homography_parquet_roundtrip(tmp_path: Path) -> None:
    res = stitch([seg(0, 0, 2000, {1: (range(0, 10), still(100), 0)}, ball=range(0, 5))])
    p = write_tracks(tmp_path / "tracks.parquet", res.columns, {"stage": "S5"})
    t = read_tracks(p)
    assert tuple(t.column_names) == TRACK_COLUMNS and t.num_rows == 15
    assert str(t.schema.field("h_quality").type) == "halffloat" and str(t.schema.field("track_id").type) == "int32"
    with pytest.raises(ValueError, match="missing"):
        tracks_table({"frame_idx": [1]})
    H = np.repeat(np.eye(3)[None], 3, axis=0)
    H[1] = np.nan
    hp = write_homography(tmp_path / "homography.parquet", [7, 8, 9], H, [0.9, 0.0, 0.7])
    f, H2, q = read_homography(hp)
    assert list(f) == [7, 8, 9] and np.array_equal(H2, H, equal_nan=True) and q[2] == pytest.approx(0.7)
    with pytest.raises(ValueError, match="not a tracks file"):
        read_tracks(hp)
