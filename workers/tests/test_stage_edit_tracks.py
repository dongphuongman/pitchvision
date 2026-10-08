"""M2-BE-01 track corrections: merge / split / remove on tracks.parquet, idempotent re-application."""
from __future__ import annotations

import numpy as np
import pytest
from pitchvision_worker.stages.edit_tracks import EditInvalid, apply_corrections


def _cols() -> dict[str, np.ndarray]:
    """Track 1 (team 0) frames 0–9, track 2 (team 1, goalkeeper) frames 5–14, track 3 frames 0–4, ball frames 0–14."""
    rows = [(f, 1, 0, 0) for f in range(10)] + [(f, 2, 1, 1) for f in range(5, 15)] + [(f, 3, 0, 0) for f in range(5)]
    rows += [(f, -1, -1, 3) for f in range(15)]
    a = np.array(rows)
    return {"frame_idx": a[:, 0], "ts_ms": a[:, 0] * 40, "track_id": a[:, 1], "team": a[:, 2], "cls": a[:, 3],
            "px": a[:, 0] * 1.0}


def _frames(c: dict[str, np.ndarray], tid: int) -> list[int]:
    return sorted(c["frame_idx"][c["track_id"] == tid].tolist())


OPS = [{"type": "merge", "from": 3, "into": 2},           # 3 (frames 0–4) joins 2 (5–14): no overlap
       {"type": "split", "track": 1, "at_ms": 240, "new_track": 50},  # frames 6–9 of track 1 → 50
       {"type": "remove", "track": 50, "from_ms": 320, "to_ms": 360},  # frame 8 of the new track
       {"type": "remove", "track": 99}]                    # unknown track: a no-op


def test_operations() -> None:
    c, applied, skipped = apply_corrections(_cols(), OPS)
    assert (applied, skipped) == (3, 1)
    assert _frames(c, 2) == list(range(15)) and _frames(c, 3) == []
    assert set(c["team"][c["track_id"] == 2]) == {1} and set(c["cls"][c["track_id"] == 2]) == {1}  # into's team
    assert _frames(c, 1) == list(range(6)) and _frames(c, 50) == [6, 7, 9]
    assert _frames(c, -1) == list(range(15))  # the ball is untouched
    assert np.all(np.diff(c["frame_idx"]) >= 0)  # still sorted by frame


def test_reapplying_the_list_changes_nothing() -> None:
    once, _, _ = apply_corrections(_cols(), OPS)
    twice, applied, skipped = apply_corrections(once, OPS)
    assert applied == 0 and skipped == len(OPS)
    for k in once:
        np.testing.assert_array_equal(once[k], twice[k])


def test_merge_overlap_keeps_the_target_rows() -> None:
    c, _, _ = apply_corrections(_cols(), [{"type": "merge", "from": 1, "into": 2}])
    assert _frames(c, 2) == list(range(15)) and len(c["frame_idx"][c["track_id"] == 2]) == 15  # 5–9 not doubled


def test_whole_track_removal_and_unknown_type() -> None:
    c, applied, _ = apply_corrections(_cols(), [{"type": "remove", "track": 3}])
    assert applied == 1 and _frames(c, 3) == []
    with pytest.raises(EditInvalid):
        apply_corrections(_cols(), [{"type": "rename", "track": 1}])
