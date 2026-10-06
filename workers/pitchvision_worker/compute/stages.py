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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pitchvision_schemas import ErrorCode, PipelineConfig, Segment, ShotTimeline
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


def _plan_segments(timeline: ShotTimeline, duration_ms: int, config: PipelineConfig) -> list[Segment]:
    plan: list[Segment] = contract_fn("segments", "plan_segments")(timeline, duration_ms, config)
    return plan


STAGES: dict[str, StageFn] = {
    "normalize": _normalize,
    "shots": _shots,
    "calibrate": _calibrate,
    "infer": _infer,
    "pitch": _pitch,
}
PLANNERS: dict[str, SegmentPlanner] = {"plan_segments": _plan_segments}
