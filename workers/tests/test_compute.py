"""M1-BE-11: ComputeBackend payloads, router, executor checkpoint + image-tag rule; SeaweedFS/Redis round trips."""

from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pipeline_fakes import WIDE, MemoryStore
from pitchvision_schemas import CpuTask, ErrorCode, GpuTask, PipelineConfig, Segment
from pitchvision_worker.celery_app import QUEUE_ORCHESTRATE, TASK_ON_RESULT, create_app
from pitchvision_worker.compute import stages as stage_registry
from pitchvision_worker.compute.base import StageFailure, StageTask, classify, success_key
from pitchvision_worker.compute.executor import execute
from pitchvision_worker.compute.local import TASK_RUN_CPU, TASK_RUN_GPU, LocalGpuBackend
from pitchvision_worker.compute.router import BackendRouter
from pitchvision_worker.compute.runpod import RunPodBackend
from pitchvision_worker.compute.stages import StageOutput
from pitchvision_worker.compute.tasks import run_and_report
from pitchvision_worker.config import WorkerConfig
from pitchvision_worker.storage import ObjectStore
from worker_testkit import S3_BUCKET, s3_test_client

TAG = "sha-test"
PREFIX = "results/orgs/o/videos/v/jobs/j/stages/shots/"


def gpu_task(**over: Any) -> GpuTask:
    base: dict[str, Any] = {"task_id": "j:shots", "stage": "shots", "image_tag": TAG, "output_prefix": PREFIX,
                            "inputs": {"normalized": "media/orgs/o/videos/v/normalized.mp4"},
                            "config": PipelineConfig(), "timeout_s": 600}  # fmt: skip
    return GpuTask(**(base | over))


class SendRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def send_task(self, name: str, **kw: Any) -> Any:
        self.calls.append((name, kw))
        return type("R", (), {"id": f"celery-{len(self.calls)}"})()


def test_local_gpu_backend_payload_and_queue() -> None:
    rec = SendRecorder()
    task = gpu_task()
    handle = LocalGpuBackend(rec, queue="gpu").submit(task)  # type: ignore[arg-type]
    assert handle.backend == "local" and handle.external_id == "celery-1"
    name, kw = rec.calls[0]
    assert name == TASK_RUN_GPU and kw["queue"] == "gpu"
    assert kw["kwargs"] == {"task": task.model_dump(mode="json")}
    assert GpuTask.model_validate(kw["kwargs"]["task"]) == task  # payload round-trips (JSON serializer)
    assert kw["soft_time_limit"] == 600 and kw["time_limit"] > 600


def test_router_follows_config() -> None:
    rec = SendRecorder()
    cfg = replace(WorkerConfig.from_env({}), local_gpu_queue="cpu")  # CPU-only compose: GPU stages on `cpu`
    router = BackendRouter.from_config(cfg, rec)  # type: ignore[arg-type]
    router.for_task(gpu_task()).submit(gpu_task())
    cpu = CpuTask(task_id="j:normalize", stage="normalize", image_tag=TAG, output_prefix="p/", config=PipelineConfig())
    router.for_task(cpu).submit(cpu)
    assert [(n, kw["queue"]) for n, kw in rec.calls] == [(TASK_RUN_GPU, "cpu"), (TASK_RUN_CPU, "cpu")]
    assert WorkerConfig.from_env({"PV_LOCAL_GPU_QUEUE": "cpu"}).local_gpu_queue == "cpu"
    assert WorkerConfig.from_env({}).local_gpu_queue == "gpu"

    runpod_first = BackendRouter.from_config(replace(cfg, compute_backends=("runpod",)), rec)  # type: ignore[arg-type]
    backend = runpod_first.for_task(gpu_task())
    assert isinstance(backend, RunPodBackend)
    with pytest.raises(NotImplementedError):
        backend.submit(gpu_task())
    with pytest.raises(ValueError, match="not available"):
        BackendRouter.from_config(replace(cfg, compute_backends=("k8s",)), rec)  # type: ignore[arg-type]


def _fake_shots(calls: list[str]) -> Any:
    def run(task: StageTask, inputs: dict[str, Path], out_dir: Path) -> StageOutput:
        calls.append(task.task_id)
        assert inputs["normalized"].read_bytes() == b"video"
        (out_dir / "shots.json").write_text(WIDE.model_dump_json())
        (out_dir / "debug").mkdir()
        (out_dir / "debug" / "grid.jpg").write_bytes(b"jpg")
        return StageOutput({"shots": 2})

    return run


def test_executor_checkpoint_and_image_tag() -> None:
    store = MemoryStore()
    store.put_bytes("media/orgs/o/videos/v/normalized.mp4", b"video")
    calls: list[str] = []
    stages = {"shots": _fake_shots(calls)}
    result = execute(gpu_task(), store, image_tag=TAG, stages=stages)  # type: ignore[arg-type]
    assert result["shots"] == 2 and result["outputs"] == [PREFIX + "debug/grid.jpg", PREFIX + "shots.json"]
    assert json.loads(store.get_bytes(success_key(PREFIX))) == result
    assert execute(gpu_task(), store, image_tag=TAG, stages=stages) == result  # type: ignore[arg-type]
    assert calls == ["j:shots"]  # second run skipped on _SUCCESS
    with pytest.raises(StageFailure) as e:
        execute(gpu_task(output_prefix="x/"), store, image_tag="other", stages=stages)  # type: ignore[arg-type]
    assert e.value.code == ErrorCode.INTERNAL and e.value.retryable is False
    with pytest.raises(StageFailure, match="no stage"):
        execute(gpu_task(output_prefix="y/"), store, image_tag=TAG, stages={})  # type: ignore[arg-type]


def test_missing_cv_stage_is_a_clear_non_retryable_error(tmp_path: Path) -> None:
    import importlib.util

    if importlib.util.find_spec("pitchvision_worker.stages.calibrate") is not None:
        pytest.skip("real calibrate stage installed")
    store = MemoryStore()
    for k in ("n.mp4", "s.json"):
        store.put_bytes(k, WIDE.model_dump_json().encode())
    task = gpu_task(stage="calibrate", task_id="j:calibrate", inputs={"normalized": "n.mp4", "shots": "s.json"})
    with pytest.raises(StageFailure, match=r"stages\.calibrate\.run_calibrate unavailable") as e:
        execute(task, store, image_tag=TAG)  # type: ignore[arg-type]
    assert e.value.retryable is False


def test_classify() -> None:
    from cv_core.io.normalize import FFmpegError, MediaError

    assert classify(MediaError("NO_VIDEO_STREAM", "x")).code == ErrorCode.VIDEO_UNREADABLE
    assert classify(FFmpegError("killed")).retryable is True

    class Coded(Exception):
        code = "HOMOGRAPHY_UNAVAILABLE"
        retryable = False

    assert (classify(Coded()).code, classify(Coded()).retryable) == (ErrorCode.HOMOGRAPHY_UNAVAILABLE, False)
    assert classify(KeyError("x")).code == ErrorCode.INTERNAL and classify(KeyError("x")).retryable


# ---------------------------------------------------------------- real SeaweedFS (+ Redis)
@pytest.fixture
def s3_store(s3_endpoint: str) -> ObjectStore:
    store = ObjectStore(s3_test_client(s3_endpoint), S3_BUCKET)
    store.put_bytes("media/orgs/o/videos/v/normalized.mp4", b"video", "video/mp4")
    for key in [success_key(PREFIX), PREFIX + "shots.json", PREFIX + "debug/grid.jpg"]:
        store.client.delete_object(Bucket=S3_BUCKET, Key=key)
    return store


def test_round_trip_over_seaweedfs(s3_store: ObjectStore, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setitem(stage_registry.STAGES, "shots", _fake_shots(calls))
    reports: list[tuple[str, str, dict[str, Any]]] = []
    cfg = replace(WorkerConfig.from_env({}), image_tag=TAG)
    out = run_and_report(gpu_task(), "ext-1", s3_store, cfg, notify=lambda *a: reports.append(a))
    assert out["ok"] is True and reports == [("j:shots", "ext-1", out)]
    assert s3_store.exists(PREFIX + "shots.json") and s3_store.exists(PREFIX + "debug/grid.jpg")
    assert json.loads(s3_store.get_bytes(success_key(PREFIX)))["shots"] == 2
    head = s3_store.client.head_object(Bucket=S3_BUCKET, Key=PREFIX + "shots.json")
    assert head["ContentType"] == "application/json"
    again = run_and_report(gpu_task(), "ext-2", s3_store, cfg, notify=lambda *a: reports.append(a))
    assert again["result"] == out["result"] and calls == ["j:shots"]  # checkpoint skip
    bad = run_and_report(gpu_task(image_tag="old"), "ext-3", s3_store, cfg, notify=lambda *a: reports.append(a))
    assert bad["ok"] is False and bad["error"]["code"] == "INTERNAL" and bad["error"]["retryable"] is False


def test_local_backend_celery_round_trip(redis_url: str, s3_store: ObjectStore,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """LocalGpuBackend → gpu.run_stage on queue `cpu` (CPU-only box) → orchestrate.on_task_result, over real Redis."""
    import gc

    from celery.contrib.testing.worker import start_worker

    calls: list[str] = []
    monkeypatch.setitem(stage_registry.STAGES, "shots", _fake_shots(calls))
    app = create_app(redis_url)
    cfg = replace(WorkerConfig.from_env({}), image_tag=TAG)
    reports: list[dict[str, Any]] = []

    def notify(task_id: str, external_id: str, outcome: dict[str, Any]) -> None:
        app.send_task(TASK_ON_RESULT, kwargs={"task_id": task_id, "external_id": external_id, **outcome},
                      queue=QUEUE_ORCHESTRATE)  # fmt: skip

    # the real tasks are `shared` Celery tasks (added to every app on finalize): swap in test doubles by name
    app.finalize()
    for name in (TASK_RUN_GPU, TASK_ON_RESULT):
        if name in app.tasks:
            app.tasks.unregister(name)

    @app.task(name=TASK_RUN_GPU, bind=True, shared=False)
    def run_stage(self: Any, task: dict[str, Any]) -> dict[str, Any]:
        return run_and_report(GpuTask.model_validate(task), str(self.request.id), s3_store, cfg, notify)

    @app.task(name=TASK_ON_RESULT, shared=False)
    def on_result(**kw: Any) -> None:
        reports.append(kw)

    backend = LocalGpuBackend(app, queue="cpu")
    with start_worker(app, queues=["cpu", QUEUE_ORCHESTRATE], pool="solo", perform_ping_check=False,
                      loglevel="WARNING"):  # fmt: skip
        handle = backend.submit(gpu_task())
        deadline = time.monotonic() + 30
        while (backend.status(handle) != "done" or not reports) and time.monotonic() < deadline:
            time.sleep(0.2)
        status = backend.status(handle)
        gc.collect()
    assert status == "done" and calls == ["j:shots"]
    assert reports and reports[0]["task_id"] == "j:shots" and reports[0]["external_id"] == handle.external_id
    assert reports[0]["ok"] is True and s3_store.exists(success_key(PREFIX))


def test_infer_adapter_takes_the_segment_from_the_task(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[Segment] = []

    def run_infer(normalized: Path, segment: Segment, team_model: Path, out_dir: Path, config: PipelineConfig) -> dict:
        seen.append(segment)
        return {"frames": 3}

    monkeypatch.setattr(stage_registry, "contract_fn", lambda module, name: run_infer)
    seg = Segment(index=7, t0_ms=840_000, t1_ms=960_000)
    task = GpuTask(task_id=GpuTask.make_id("j", "infer", 7), stage="infer", image_tag=TAG, output_prefix="p/",
                   config=PipelineConfig(), segment=seg)  # fmt: skip
    inputs = {"normalized": tmp_path / "n.mp4", "team_model": tmp_path / "t.json"}
    out = stage_registry.STAGES["infer"](task, inputs, tmp_path)
    assert seen == [seg] and out.result == {"frames": 3, "segment": seg.model_dump()}

    # a payload queued before GpuTask.segment existed (segments.json input + index in the task id) still resolves
    legacy = task.model_copy(update={"segment": None})
    (tmp_path / "segments.json").write_text(json.dumps({"segments": [seg.model_dump()]}))
    stage_registry.STAGES["infer"](legacy, inputs | {"segments": tmp_path / "segments.json"}, tmp_path)
    assert seen[-1] == seg
    with pytest.raises(StageFailure) as e:
        stage_registry.STAGES["infer"](legacy, inputs, tmp_path)
    assert e.value.code is ErrorCode.INTERNAL and not e.value.retryable


def test_stage_of_a_finished_job_is_skipped(s3_store: Any) -> None:
    reports: list[Any] = []
    cfg = WorkerConfig.from_env({})
    out = run_and_report(gpu_task(), "ext-9", s3_store, cfg, notify=lambda *a: reports.append(a),
                         job_status=lambda job, _cfg: "cancelled")  # fmt: skip
    assert out["ok"] is False and out["skipped"] is True and "cancelled" in out["error"]["detail"]
    assert reports and reports[0][2] is out
