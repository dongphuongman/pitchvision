"""TEST-ONLY fakes for the pipeline: in-memory object store, recording compute backend, and DEV FAKE stages.

The fake stages stand in for stages/calibrate.py, stages/infer.py and stages/segments.py (CV track, M1-CV-08…10)
and for the real normalise/shots (which need ffmpeg / model weights). They are never registered outside tests.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pitchvision_schemas import Capacity, PipelineConfig, Segment, Shot, ShotTimeline, ShotType, TaskHandle, TaskStatus
from pitchvision_worker.compute.base import StageTask
from pitchvision_worker.compute.stages import SHOTS_FILE, StageOutput, task_segment


class MemoryStore:
    """ObjectStore duck type over a dict."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def exists(self, key: str) -> bool:
        return key in self.objects

    def get_bytes(self, key: str) -> bytes:
        return self.objects[key]

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/json") -> None:
        self.objects[key] = data

    def download(self, key: str, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.objects[key])
        return path

    def upload(self, key: str, path: Path) -> int:
        self.objects[key] = path.read_bytes()
        return len(self.objects[key])

    def copy(self, src: str, dst: str) -> None:
        self.objects[dst] = self.objects[src]


@dataclass
class FakeBackend:
    """ComputeBackend that only records submissions; the test decides when/how they run."""

    name: str = "local"
    submitted: list[tuple[str, StageTask]] = field(default_factory=list)
    states: dict[str, TaskStatus] = field(default_factory=dict)
    cancelled: list[str] = field(default_factory=list)
    n: int = 0

    def submit(self, task: StageTask) -> TaskHandle:
        self.n += 1
        ext = f"ext-{self.n}"
        self.submitted.append((ext, task))
        return TaskHandle(backend=self.name, external_id=ext)

    def status(self, h: TaskHandle) -> TaskStatus:
        return self.states.get(h.external_id, "queued")

    def cancel(self, h: TaskHandle) -> None:
        self.cancelled.append(h.external_id)

    def capacity(self) -> Capacity:
        return Capacity(free_slots=8, queue_depth=0, queue_wait_est_s=0)


@dataclass
class Clock:
    now: datetime = field(default_factory=lambda: datetime(2026, 10, 6, 12, 0, tzinfo=UTC))

    def __call__(self) -> datetime:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@dataclass
class FakeRedis:
    published: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, json.loads(message)))
        return 1


DURATION_MS = 600_000
WIDE = ShotTimeline(shots=[Shot(t0_ms=0, t1_ms=400_000, type=ShotType.main_wide, conf=0.9),
                           Shot(t0_ms=400_000, t1_ms=DURATION_MS, type=ShotType.close_up, conf=0.8)])  # fmt: skip
NO_WIDE = ShotTimeline(shots=[Shot(t0_ms=0, t1_ms=DURATION_MS, type=ShotType.close_up, conf=0.8)])


@dataclass
class DevFakeStages:
    """DEV FAKE stages (test only). `fail` maps stage/segment key → callable raising; `calls` counts runs."""

    timeline: ShotTimeline = field(default_factory=lambda: WIDE)
    fail: dict[str, Callable[[], None]] = field(default_factory=dict)
    calls: dict[str, int] = field(default_factory=dict)

    def _run(self, key: str) -> None:
        self.calls[key] = self.calls.get(key, 0) + 1
        if key in self.fail:
            self.fail[key]()

    def normalize(self, task: StageTask, inputs: dict[str, Path], out_dir: Path) -> StageOutput:
        self._run("normalize")
        out = out_dir / "normalized.mp4"
        out.write_bytes(b"normalized:" + inputs["source"].read_bytes())
        manifest = {"fps": 25, "width": 1280, "height": 720, "duration_ms": DURATION_MS, "frame_count": 15000}
        source = {"fps": 50.0, "width": 1920, "height": 1080, "duration_ms": DURATION_MS}
        return StageOutput({"manifest": manifest, "source": source},
                           uploads={getattr(task, "outputs", {})["normalized"]: out})  # fmt: skip

    def shots(self, task: StageTask, inputs: dict[str, Path], out_dir: Path) -> StageOutput:
        self._run("shots")
        (out_dir / SHOTS_FILE).write_text(self.timeline.model_dump_json())
        return StageOutput({"shots": len(self.timeline.shots)})

    def calibrate(self, task: StageTask, inputs: dict[str, Path], out_dir: Path) -> StageOutput:
        self._run("calibrate")
        (out_dir / "team_model.json").write_text(json.dumps({"teams": 2}))
        return StageOutput({"teams": 2})

    def infer(self, task: StageTask, inputs: dict[str, Path], out_dir: Path) -> StageOutput:
        segment = task_segment(task, inputs)  # GpuTask.segment (the orchestrator no longer ships segments.json)
        assert "segments" not in inputs and task.task_id.endswith(f":{segment.index:03d}")
        self._run(f"infer:{segment.index:03d}")
        assert inputs["team_model"].read_text() == json.dumps({"teams": 2})
        (out_dir / "detections.parquet").write_bytes(b"PAR1-fake")
        return StageOutput({"frames": 10})

    def registry(self) -> dict[str, Any]:
        return {"normalize": self.normalize, "shots": self.shots, "calibrate": self.calibrate, "infer": self.infer}


def plan_every_120s(timeline: ShotTimeline, duration_ms: int, config: PipelineConfig) -> list[Segment]:
    """DEV FAKE planner: main_wide shots cut into `segment_target_s` chunks."""
    out: list[Segment] = []
    step = config.segment_target_s * 1000
    for s in timeline.shots:
        if s.type != ShotType.main_wide:
            continue
        t = s.t0_ms
        while t < s.t1_ms:
            out.append(Segment(index=len(out), t0_ms=t, t1_ms=min(t + step, s.t1_ms)))
            t += step
    return out


def new_id() -> uuid.UUID:
    return uuid.uuid4()
