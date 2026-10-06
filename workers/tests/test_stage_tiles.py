"""S7 tiles (M1-BE-13): tracks.parquet + ShotTimeline → gzip JSON tiles that round-trip through `Tile`."""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest
from pipeline_fakes import synthetic_detections
from pitchvision_schemas import TILE_MS, CpuTask, PipelineConfig, Segment, Shot, ShotTimeline, ShotType, Tile, keys
from pitchvision_worker.compute.stages import STAGES
from pitchvision_worker.stages.stitch import SegmentInput, run_stitch
from pitchvision_worker.stages.tiles import decode_tile, run_tiles, tile_count_for

DURATION = 41_500
TIMELINE = ShotTimeline(shots=[Shot(t0_ms=0, t1_ms=25_000, type=ShotType.main_wide, conf=0.9),
                               Shot(t0_ms=25_000, t1_ms=32_000, type=ShotType.replay, conf=0.8),
                               Shot(t0_ms=32_000, t1_ms=DURATION, type=ShotType.main_wide, conf=0.9)])  # fmt: skip
SEGMENTS = [Segment(index=0, t0_ms=0, t1_ms=12_000), Segment(index=1, t0_ms=12_000, t1_ms=25_000),
            Segment(index=2, t0_ms=32_000, t1_ms=DURATION)]  # fmt: skip


@pytest.fixture(scope="module")
def tracks(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("tiles")
    inputs = [SegmentInput(s, synthetic_detections(d / f"det{s.index}.parquet", s, players=3)) for s in SEGMENTS]
    return run_stitch(inputs, TIMELINE, d / "stitch").paths["tracks"]


def test_tiles_cover_the_whole_duration(tracks: Path, tmp_path: Path) -> None:
    out = run_tiles(tracks, TIMELINE, tmp_path, PipelineConfig(), duration_ms=DURATION)
    assert out.tile_count == tile_count_for(DURATION) == 5 and sorted(out.paths) == [0, 1, 2, 3, 4]
    tiles = [decode_tile(out.paths[n].read_bytes()) for n in range(5)]
    frames = [f.f for t in tiles for f in t.frames]
    assert frames == list(range(1038))  # every frame of the video, once, in order (41.5 s × 25 fps)
    assert out.frames == 1038 and out.objects == 4 * (1038 - 175)  # 3 players + ball on analysed frames
    for n, t in enumerate(tiles):
        assert (t.v, t.tile_no, t.fps, t.t0_ms) == (1, n, 25, n * TILE_MS)
        assert t.t1_ms == min((n + 1) * TILE_MS, DURATION)
        assert t.frames[0].f == n * 250
        # the shot runs cover exactly the tile's frames
        assert t.shots[0][0] == t.frames[0].f and t.shots[-1][1] == t.frames[-1].f + 1
        assert all(a[1] == b[0] for a, b in zip(t.shots, t.shots[1:], strict=False))
    t2 = tiles[2]  # 20–30 s: wide until 25 s, then the replay
    assert [list(r) for r in t2.shots] == [[500, 625, ShotType.main_wide], [625, 750, ShotType.replay]]
    replay = [f for f in t2.frames if f.f >= 625]
    assert all(f.objs == [] and f.hq is None for f in replay)
    wide = t2.frames[0]
    assert len(wide.objs) == 4 and sorted(o[0] for o in wide.objs) == [-1, 1, 2, 3]
    ball = next(o for o in wide.objs if o[0] == -1)
    assert ball[1] == 3 and ball[2] == -1 and ball[8] is None and ball[9] is None
    assert tiles[4].frames[-1].f == 1037 and tiles[4].shots == [(1000, 1038, ShotType.main_wide)]
    # player ids continue across the 12 s keyframe boundary, new ids after the replay cut (no ReID in M1)
    ids_before = {o[0] for f in tiles[0].frames for o in f.objs}
    ids_after = {o[0] for f in tiles[4].frames for o in f.objs}
    assert ids_before == {-1, 1, 2, 3} and ids_after == {-1, 4, 5, 6}
    assert {o[0] for f in tiles[1].frames for o in f.objs} == {-1, 1, 2, 3}


def test_tiles_are_deterministic_and_raw_json_matches_schema(tracks: Path, tmp_path: Path) -> None:
    a = run_tiles(tracks, TIMELINE, tmp_path / "a", duration_ms=DURATION)
    b = run_tiles(tracks, TIMELINE, tmp_path / "b", duration_ms=DURATION)
    for n in a.paths:
        assert a.paths[n].read_bytes() == b.paths[n].read_bytes()
    raw = json.loads(gzip.decompress(a.paths[0].read_bytes()))
    assert set(raw) == {"v", "tile_no", "t0_ms", "t1_ms", "fps", "shots", "frames"}
    assert len(raw["frames"][0]["objs"][0]) == 10
    Tile.model_validate(raw)


def test_tiles_adapter_uploads_to_tile_keys(tracks: Path, tmp_path: Path) -> None:
    shots = tmp_path / "shots.json"
    shots.write_text(TIMELINE.model_dump_json())
    params = {"org_id": "o1", "video_id": "v1", "job_id": "j1", "revision": 2, "duration_ms": DURATION,
              "frame_count": 1038}
    task = CpuTask(task_id="j1:tiles", stage="tiles", image_tag="t", output_prefix="results/x/stages/tiles/",
                   config=PipelineConfig(), params=params)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    res = STAGES["tiles"](task, {"tracks": tracks, "shots": shots}, out_dir)
    assert res.uploads is not None
    assert sorted(res.uploads) == [keys.tile_key("o1", "v1", "j1", 2, n) for n in range(5)]
    assert res.result["tile_count"] == 5 and res.result["revision"] == 2
    assert keys.tile_key("o1", "v1", "j1", 2, 3).startswith(keys.tile_prefix("o1", "v1", "j1", 2))
