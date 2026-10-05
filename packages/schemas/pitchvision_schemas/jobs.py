"""Job state machine (AD §16.1)."""
from enum import StrEnum


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
