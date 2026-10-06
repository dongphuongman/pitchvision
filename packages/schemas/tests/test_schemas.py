import pytest
from pitchvision_schemas import (
    ErrorCode,
    GpuTask,
    JobStatus,
    PipelineConfig,
    Problem,
    ServerEvent,
    Shot,
    ShotTimeline,
    ShotType,
    Tile,
    can_transition,
    tile_key,
    tile_no_for,
)
from pydantic import TypeAdapter, ValidationError


def timeline() -> ShotTimeline:
    return ShotTimeline(shots=[
        Shot(t0_ms=0, t1_ms=60_000, type=ShotType.main_wide, conf=0.98),
        Shot(t0_ms=60_000, t1_ms=70_000, type=ShotType.replay, conf=0.9),
        Shot(t0_ms=70_000, t1_ms=100_000, type=ShotType.main_wide, conf=0.97),
    ])


def test_shot_timeline_coverage_and_lookup():
    tl = timeline()
    assert tl.coverage() == pytest.approx(0.9)
    assert tl.at(65_000).type is ShotType.replay
    assert tl.at(0).type is ShotType.main_wide
    assert tl.at(100_000) is None


def test_shot_timeline_rejects_gaps():
    with pytest.raises(ValidationError):
        ShotTimeline(shots=[Shot(t0_ms=0, t1_ms=10, type="main_wide", conf=1),
                            Shot(t0_ms=20, t1_ms=30, type="replay", conf=1)])


def test_job_state_machine():
    assert can_transition(JobStatus.queued, JobStatus.ingesting)
    assert can_transition(JobStatus.inferring, JobStatus.failed)
    assert can_transition(JobStatus.failed, JobStatus.inferring)  # retry resumes
    assert not can_transition(JobStatus.queued, JobStatus.done)
    assert not can_transition(JobStatus.done, JobStatus.queued)


def test_gpu_task_id_and_config_is_frozen():
    t = GpuTask(task_id=GpuTask.make_id("j1", "infer", 17), stage="infer", image_tag="abc",
                output_prefix="orgs/o/videos/v/jobs/j1/segments/017/", config=PipelineConfig())
    assert t.task_id == "j1:infer:017"
    assert t.config.detector == "dfine-m" and t.config.target_fps == 25
    with pytest.raises(ValidationError):
        PipelineConfig(unknown=1)


def test_tile_roundtrip_and_keys():
    tile = Tile(tile_no=3, t0_ms=30_000, t1_ms=40_000, fps=25, shots=[(750, 1000, "main_wide")],
                frames=[{"f": 750, "hq": 0.9, "objs": [[7, 0, 0, 10, 20, 30, 80, 0.9, 34.2, 41.7]]},
                        {"f": 751, "hq": None, "objs": [[7, 0, 0, 10, 20, 30, 80, 0.9, None, None]]}])
    assert Tile.model_validate_json(tile.model_dump_json()) == tile
    assert tile_no_for(39_999) == 3
    assert tile_key("o", "v", "j", 2, 3) == "tiles/orgs/o/videos/v/jobs/j/r2/000003.json.gz"


def test_ws_events_discriminate():
    ev = TypeAdapter(ServerEvent).validate_python(
        {"topic": "job:7f3a1b2c", "type": "failed", "data": {"code": "HOMOGRAPHY_UNAVAILABLE", "retryable": False}})
    assert ev.type == "failed" and ev.data.code is ErrorCode.HOMOGRAPHY_UNAVAILABLE


@pytest.mark.parametrize("frame", [
    {"topic": "job:7f3a1b2c", "type": "cancelled", "data": {"video_id": "v1"}},
    {"topic": "job:7f3a1b2c", "type": "subscribed", "data": {"status": "inferring"}},
    {"topic": "job:7f3a1b2c", "type": "unsubscribed"},
    {"type": "ping", "ts": 1_791_000_000},
    {"type": "pong"},
    {"type": "error", "topic": "job:7f3a1b2c", "data": {"code": "NOT_FOUND", "detail": "job not found"}},
    {"type": "error", "data": {"code": "VALIDATION_FAILED"}},
])  # fmt: skip
def test_ws_control_messages_and_cancelled_validate(frame):
    ev = TypeAdapter(ServerEvent).validate_python(frame)
    assert ev.type == frame["type"]
    again = TypeAdapter(ServerEvent).validate_json(ev.model_dump_json())  # what the API sends round-trips
    assert again == ev


def test_ws_rejects_unknown_type_and_bad_status():
    with pytest.raises(ValidationError):
        TypeAdapter(ServerEvent).validate_python({"topic": "job:x", "type": "exploded", "data": {}})
    with pytest.raises(ValidationError):
        TypeAdapter(ServerEvent).validate_python({"topic": "job:x", "type": "subscribed", "data": {"status": "nope"}})


def test_ws_server_event_schema_requires_type_on_every_variant():
    from pitchvision_schemas import export

    schema = TypeAdapter(ServerEvent).json_schema(mode="serialization")
    variants = {name: d for name, d in schema["$defs"].items() if "type" in d.get("properties", {})}
    assert set(variants) >= {"ProgressEvent", "CompletedEvent", "FailedEvent", "CancelledEvent", "SubscribedMessage",
                             "UnsubscribedMessage", "PingMessage", "PongMessage", "ErrorMessage"}  # fmt: skip
    assert all("type" in d["required"] for d in variants.values())
    assert set(schema["discriminator"]["mapping"]) == {
        "progress", "completed", "failed", "cancelled", "subscribed", "unsubscribed", "ping", "pong", "error"}
    assert "ws_server_event.schema.json" in export.render()


def test_gpu_task_carries_its_segment_and_cpu_task_lives_in_schemas():
    from pitchvision_schemas import CpuTask, Segment

    seg = Segment(index=3, t0_ms=240_000, t1_ms=360_000)
    t = GpuTask(task_id=GpuTask.make_id("j", "infer", 3), stage="infer", image_tag="x", output_prefix="p/",
                config=PipelineConfig(), segment=seg)  # fmt: skip
    assert GpuTask.model_validate(t.model_dump(mode="json")).segment == seg
    assert GpuTask(task_id="j:shots", stage="shots", image_tag="x", output_prefix="p/", config=PipelineConfig()).segment \
        is None
    cpu = CpuTask(task_id=CpuTask.make_id("j", "normalize"), stage="normalize", image_tag="x", output_prefix="p/",
                  outputs={"normalized": "media/…/normalized.mp4"}, config=PipelineConfig())  # fmt: skip
    assert cpu.task_id == "j:normalize" and cpu.timeout_s == 3600
    with pytest.raises(ValidationError):
        CpuTask(task_id="j:x", stage="infer", image_tag="x", output_prefix="p/", config=PipelineConfig())


def test_problem_type_url():
    p = Problem.of(ErrorCode.QUOTA_EXCEEDED, 402, "Monthly processing quota exceeded")
    assert p.type == "https://docs.pitchvision.ai/errors/quota-exceeded"


def test_exported_json_schema_is_current():
    from pitchvision_schemas import export
    assert export.main.__module__  # module imports
    import sys
    argv = sys.argv
    sys.argv = ["export", "--check"]
    try:
        assert export.main() == 0
    finally:
        sys.argv = argv


def test_key_layout_puts_storage_class_first():
    from pitchvision_schemas import keys

    assert keys.source_key("o", "v", ".MP4") == "source/orgs/o/videos/v/original.mp4"
    assert keys.segment_prefix("o", "v", "j", 7) == "segments/orgs/o/videos/v/jobs/j/007/"
    # every key a video owns sits under one of video_prefixes (erasure deletes all of them)
    assert keys.shots_key("o", "v") == "media/orgs/o/videos/v/shots.json"
    owned = [keys.source_key("o", "v", "mp4"), keys.normalized_key("o", "v"), keys.manifest_key("o", "v"),
             keys.shots_key("o", "v"),
             keys.segment_prefix("o", "v", "j", 0), keys.job_results_prefix("o", "v", "j"),
             keys.tile_key("o", "v", "j", 0, 0), keys.export_key("o", "v", "e", "mp4")]
    prefixes = keys.video_prefixes("o", "v")
    assert all(any(k.startswith(p) for p in prefixes) for k in owned)
    # the lifecycle rules in infra/terraform target exactly these classes
    assert keys.source_key("o", "v", "mp4").startswith("source/")
    assert keys.segment_prefix("o", "v", "j", 0).startswith("segments/")
