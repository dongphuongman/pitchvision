"""Stage registry: task stage name → adapter around the pure stage function (docs/contracts/pipeline-stages.md).

An adapter receives the task, its inputs downloaded to local paths and an empty output dir, calls the stage
function and returns a JSON-able result summary (+ optional explicit uploads). The executor does all I/O.

``calibrate``, ``infer``, ``pitch`` and ``plan_segments`` are resolved lazily from ``pitchvision_worker.stages.
{calibrate,infer,pitch,segments}`` (written by the CV track): an image without them fails the task with a clear
non-retryable INTERNAL error instead of failing at import. Tests swap entries of ``STAGES`` / ``PLANNERS`` for fakes.
"""

from __future__ import annotations

import dataclasses
import importlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pitchvision_schemas import ErrorCode, PipelineConfig, Segment, ShotTimeline, keys
from pydantic import BaseModel

from pitchvision_worker.compute.base import StageFailure, StageTask


@dataclass
class StageOutput:
    result: dict[str, Any]
    uploads: dict[str, Path] | None = None  # object key → local file; None = every file in out_dir under output_prefix


StageFn = Callable[[StageTask, dict[str, Path], Path], StageOutput]
SegmentPlanner = Callable[[ShotTimeline, int, PipelineConfig], list[Segment]]

SHOTS_FILE = "shots.json"  # under the shots task output_prefix; copied to keys.shots_key once planned


def contract_fn(module: str, name: str) -> Callable[..., Any]:
    """`pitchvision_worker.stages.<module>.<name>`, or a non-retryable INTERNAL failure if this image lacks it."""
    try:
        mod = importlib.import_module(f"pitchvision_worker.stages.{module}")
        fn: Callable[..., Any] = getattr(mod, name)
        return fn
    except (ImportError, AttributeError) as e:
        raise StageFailure(ErrorCode.INTERNAL, f"stage function stages.{module}.{name} unavailable: {e}",
                           retryable=False) from None  # fmt: skip


def jsonable(obj: Any) -> Any:
    """Stage result objects → JSON (pydantic models, dataclasses, paths, tuples)."""
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [jsonable(v) for v in obj]
    if isinstance(obj, Path):
        return obj.name  # local temp paths mean nothing to the orchestrator
    if obj is None or isinstance(obj, str | int | float | bool):
        return obj
    return str(obj)


def segment_index(task_id: str) -> int:
    """GpuTask.make_id(job, "infer", 17) → 17 (legacy payloads only; new tasks carry GpuTask.segment)."""
    return int(task_id.rsplit(":", 1)[1])


# ---------------------------------------------------------------- adapters
def _normalize(task: StageTask, inputs: dict[str, Path], out_dir: Path) -> StageOutput:
    from pitchvision_worker.stages.normalize import run_normalize

    out = run_normalize(inputs["source"], out_dir, task.config, timeout_s=float(task.timeout_s))
    target = getattr(task, "outputs", {}).get("normalized")
    if not target:
        raise StageFailure(ErrorCode.INTERNAL, "normalize task without outputs.normalized", retryable=False)
    result = {"manifest": out.manifest, "source": out.source, "encoder": out.encoder, "elapsed_s": out.elapsed_s}
    return StageOutput(jsonable(result), uploads={target: out.path})


def _shots(task: StageTask, inputs: dict[str, Path], out_dir: Path) -> StageOutput:
    from pitchvision_worker.stages.shots import run_shots

    timeline = run_shots(inputs["normalized"], task.config)
    (out_dir / SHOTS_FILE).write_text(timeline.model_dump_json())
    return StageOutput({"shots": len(timeline.shots), "main_wide_pct": round(100 * timeline.coverage(), 2)})


def _calibrate(task: StageTask, inputs: dict[str, Path], out_dir: Path) -> StageOutput:
    run_calibrate = contract_fn("calibrate", "run_calibrate")
    timeline = ShotTimeline.model_validate_json(inputs["shots"].read_text())
    out = run_calibrate(inputs["normalized"], timeline, out_dir, task.config)
    return StageOutput(jsonable(out) if out is not None else {})


def _infer(task: StageTask, inputs: dict[str, Path], out_dir: Path) -> StageOutput:
    run_infer = contract_fn("infer", "run_infer")
    segment = task_segment(task, inputs)
    out = run_infer(inputs["normalized"], segment, inputs["team_model"], out_dir, task.config)
    result = jsonable(out) if out is not None else {}
    return StageOutput((result if isinstance(result, dict) else {"output": result}) | {"segment": segment.model_dump()})


def _pitch(task: StageTask, inputs: dict[str, Path], out_dir: Path) -> StageOutput:
    """Optional pitch calibration of one segment: result ``status`` is ``ok`` (homography.parquet written) or
    ``unavailable`` (no registered / local keypoint model — nothing written, S5 leaves px/py NaN)."""
    run_pitch = contract_fn("pitch", "run_pitch")
    segment = task_segment(task, inputs)
    cuts: list[int] = []
    if "shots" in inputs:
        timeline = ShotTimeline.model_validate_json(inputs["shots"].read_text())
        cuts = [s.t0_ms for s in timeline.shots[1:]]
    out = run_pitch(inputs["normalized"], segment, out_dir, task.config, cuts_ms=cuts)
    result = jsonable(out)
    return StageOutput((result if isinstance(result, dict) else {"output": result}) | {"segment": segment.model_dump()})


def task_segment(task: StageTask, inputs: dict[str, Path]) -> Segment:
    """The S4 segment carried by the GpuTask. Tasks queued before GpuTask.segment existed (W3 dev only) carried
    the plan as input ``segments`` + the index in the task id; they still resolve."""
    segment = getattr(task, "segment", None)
    if isinstance(segment, Segment):
        return segment
    if "segments" in inputs:
        return load_segment(inputs["segments"], segment_index(task.task_id))
    raise StageFailure(ErrorCode.INTERNAL, f"{task.task_id}: infer task without a segment", retryable=False)


def load_segment(segments_json: Path, index: int) -> Segment:
    plan = json.loads(segments_json.read_text())
    for raw in plan["segments"]:
        if raw["index"] == index:
            return Segment.model_validate(raw)
    raise StageFailure(ErrorCode.INTERNAL, f"segment {index} not in plan", retryable=False)


def detections_input(index: int) -> str:
    """Input name of segment ``index``'s detections.parquet in the stitch CpuTask."""
    return f"detections_{index:03d}"


def homography_input(index: int) -> str:
    return f"homography_{index:03d}"


def _task_params(task: StageTask) -> dict[str, Any]:
    params: dict[str, Any] = getattr(task, "params", {}) or {}
    return params


def _stitch(task: StageTask, inputs: dict[str, Path], out_dir: Path) -> StageOutput:
    """S5: params.segments = the plan; inputs shots + detections_NNN (+ homography_NNN when the pitch stage ran)."""
    run_stitch = contract_fn("stitch", "run_stitch")
    from pitchvision_worker.stages.stitch import SegmentInput

    timeline = ShotTimeline.model_validate_json(inputs["shots"].read_text())
    segments = [Segment.model_validate(s) for s in _task_params(task).get("segments") or []]
    seg_inputs = [SegmentInput(s, inputs[detections_input(s.index)], inputs.get(homography_input(s.index)))
                  for s in segments]  # fmt: skip
    out = run_stitch(seg_inputs, timeline, out_dir, task.config)
    result = jsonable(out)
    return StageOutput(result if isinstance(result, dict) else {"output": result})


def _tiles(task: StageTask, inputs: dict[str, Path], out_dir: Path) -> StageOutput:
    """S7: tiles go to keys.tile_key(org, video, job, revision, n) — outside the task's output_prefix."""
    run_tiles = contract_fn("tiles", "run_tiles")
    p = _task_params(task)
    timeline = ShotTimeline.model_validate_json(inputs["shots"].read_text())
    out = run_tiles(inputs["tracks"], timeline, out_dir, task.config, duration_ms=int(p["duration_ms"]),
                    frame_count=int(p["frame_count"]) if p.get("frame_count") else None)  # fmt: skip
    revision = int(p.get("revision") or 0)
    uploads = {keys.tile_key(str(p["org_id"]), str(p["video_id"]), str(p["job_id"]), revision, n): path
               for n, path in sorted(out.paths.items())}  # fmt: skip
    result = {"tile_count": out.tile_count, "frames": out.frames, "objects": out.objects, "bytes": out.bytes,
              "revision": revision, "stats": out.stats}  # fmt: skip
    return StageOutput(result, uploads=uploads)


class LoadDbStage:
    """S8: prepare rows (pure, ``stages.load_db``) and write them as pv_worker (``results_db``). The engine comes
    from PV_WORKER_DATABASE_URL unless a factory is injected (tests)."""

    def __init__(self, engine_factory: Callable[[], Any] | None = None) -> None:
        self.engine_factory = engine_factory

    def _engine(self) -> Any:
        if self.engine_factory is not None:
            return self.engine_factory()
        from pitchvision_worker import repository
        from pitchvision_worker.config import WorkerConfig

        return repository.engine_for(WorkerConfig.from_env().database_url)

    def __call__(self, task: StageTask, inputs: dict[str, Path], out_dir: Path) -> StageOutput:
        prepare_load = contract_fn("load_db", "prepare_load")
        from pitchvision_worker.results_db import write_results

        p = _task_params(task)
        batch = prepare_load(inputs["tracks"], inputs.get("team_model"), task.config)
        counts = write_results(self._engine(), uuid.UUID(str(p["org_id"])), uuid.UUID(str(p["video_id"])), batch)
        return StageOutput({**counts, "revision": int(p.get("revision") or 0), "stats": batch.stats})


def _plan_segments(timeline: ShotTimeline, duration_ms: int, config: PipelineConfig) -> list[Segment]:
    plan: list[Segment] = contract_fn("segments", "plan_segments")(timeline, duration_ms, config)
    return plan


STAGES: dict[str, StageFn] = {
    "normalize": _normalize,
    "shots": _shots,
    "calibrate": _calibrate,
    "infer": _infer,
    "pitch": _pitch,
    "stitch": _stitch,
    "tiles": _tiles,
    "load_db": LoadDbStage(),
}
PLANNERS: dict[str, SegmentPlanner] = {"plan_segments": _plan_segments}
