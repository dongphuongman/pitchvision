"""Run one stage task (GpuTask / CpuTask) wherever it landed: local Celery worker today, RunPod handler later.

1. refuse a task built for another image (``image_tag`` ≠ this worker's ``PV_IMAGE_TAG``: AD §7 deterministic results)
2. checkpoint: ``{output_prefix}_SUCCESS`` exists → return the result stored in it (a redelivered / retried task
   after a crash does no work twice — acks_late + visibility_timeout redeliver, AD §16.1)
3. download inputs to a temp dir, call the registered stage, upload outputs, write ``_SUCCESS`` LAST (its body is the
   result summary, so the orchestrator can recover a result whose message was lost)
"""

from __future__ import annotations

import json
import logging
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from pitchvision_schemas import ErrorCode

from pitchvision_worker.compute.base import StageFailure, StageTask, success_key
from pitchvision_worker.compute.stages import STAGES, StageFn
from pitchvision_worker.storage import ObjectStore

log = logging.getLogger(__name__)


def execute(task: StageTask, store: ObjectStore, *, image_tag: str, stages: Mapping[str, StageFn] | None = None,
            tmp_root: str | None = None) -> dict[str, Any]:  # fmt: skip
    if task.image_tag != image_tag:
        raise StageFailure(
            ErrorCode.INTERNAL,
            f"task {task.task_id} was built for image {task.image_tag!r}; this worker runs {image_tag!r}",
            retryable=False,
        )
    marker = success_key(task.output_prefix)
    if store.exists(marker):
        log.info("%s: %s exists, skipping (checkpoint)", task.task_id, marker)
        result: dict[str, Any] = json.loads(store.get_bytes(marker) or b"{}")
        return result
    fn = (stages if stages is not None else STAGES).get(task.stage)
    if fn is None:
        raise StageFailure(ErrorCode.INTERNAL, f"no stage {task.stage!r} registered in this image", retryable=False)

    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix=f"pv-{task.stage}-", dir=tmp_root) as tmp:
        root = Path(tmp)
        local = {name: store.download(key, root / "in" / name / PurePosixPath(key).name)
                 for name, key in task.inputs.items()}  # fmt: skip
        out_dir = root / "out"
        out_dir.mkdir()
        output = fn(task, local, out_dir)
        if output.uploads is not None:
            uploads = output.uploads
        else:
            prefix = task.output_prefix.rstrip("/") + "/"
            uploads = {prefix + p.relative_to(out_dir).as_posix(): p for p in sorted(out_dir.rglob("*")) if p.is_file()}
        for key, path in uploads.items():
            store.upload(key, path)
        result = {**output.result, "outputs": sorted(uploads), "elapsed_s": round(time.monotonic() - t0, 3)}
        store.put_bytes(marker, json.dumps(result).encode())  # last: the checkpoint
    log.info("%s: %s done in %.1fs, %d object(s)", task.task_id, task.stage, result["elapsed_s"], len(uploads))
    return result
