"""Job state machine (AD §16.1)."""
from enum import StrEnum
from typing import Literal


class JobStatus(StrEnum):
    queued = "queued"
    ingesting = "ingesting"
    calibrating = "calibrating"
    inferring = "inferring"
    postprocessing = "postprocessing"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


_RUNNING = [JobStatus.queued, JobStatus.ingesting, JobStatus.calibrating, JobStatus.inferring, JobStatus.postprocessing]

JOB_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    s: frozenset({nxt, JobStatus.failed, JobStatus.cancelled})
    for s, nxt in zip(_RUNNING, [*_RUNNING[1:], JobStatus.done], strict=True)
}
JOB_TRANSITIONS[JobStatus.done] = frozenset()
JOB_TRANSITIONS[JobStatus.cancelled] = frozenset()
# "retry job" resumes from the stage that failed; the orchestrator picks the stage from job_tasks
JOB_TRANSITIONS[JobStatus.failed] = frozenset(_RUNNING)


def can_transition(src: JobStatus, dst: JobStatus) -> bool:
    return dst in JOB_TRANSITIONS[src]


# What a job does (``jobs.detail.kind``; absent = ``analyze``). Derived jobs reuse the finished stages of
# ``jobs.detail.parent_job_id`` instead of recomputing them (docs/contracts/pipeline-stages.md "Derived jobs"):
# - ``team_rerun``  (SCR-14 manual teams / TEAM_SEPARATION_FAILED): reuses normalize, shots, plan; reruns S3
#                    calibrate seeded with ``detail.team_seeds``, then S4… as usual;
# - ``recalibrate`` (SCR-15 manual pitch calibration): reuses normalize, shots, plan, calibrate and the parent's
#                    tracks.parquet; runs the CPU ``recompute`` stage (re-projection with the manual H), then S7 tiles
#                    (new revision), S8 load-db and a new manifest. No GPU.
JobKind = Literal["analyze", "team_rerun", "recalibrate", "repitch"]
JOB_KINDS: tuple[JobKind, ...] = ("analyze", "team_rerun", "recalibrate", "repitch")
