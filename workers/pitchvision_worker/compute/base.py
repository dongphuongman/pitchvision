"""ComputeBackend (AD §7): where a stage task runs. Payloads are GpuTask / CpuTask; inputs/outputs are object keys.

The orchestrator never runs a GPU stage itself: it builds a task, asks the router for a backend and calls
``submit``. Completion is reported back by the executor (``orchestrate.on_task_result``) and, as a fallback that
survives lost messages and restarts, observed through ``status`` + the ``_SUCCESS`` checkpoint object.
"""

from __future__ import annotations

from typing import Any, Protocol, TypeAlias

from pitchvision_schemas import Capacity, CpuTask, ErrorCode, GpuTask, TaskHandle, TaskStatus

SUCCESS_MARKER = "_SUCCESS"


StageTask: TypeAlias = GpuTask | CpuTask  # noqa: UP040 (pydantic/isinstance-friendly); both in pitchvision_schemas.compute


def success_key(output_prefix: str) -> str:
    return output_prefix.rstrip("/") + "/" + SUCCESS_MARKER


class ComputeBackend(Protocol):
    name: str

    def submit(self, task: StageTask) -> TaskHandle: ...

    def status(self, h: TaskHandle) -> TaskStatus: ...

    def cancel(self, h: TaskHandle) -> None: ...

    def capacity(self) -> Capacity: ...


class StageFailure(Exception):
    """A stage failed in a way the orchestrator understands: an ErrorCode + whether retrying can help."""

    def __init__(self, code: ErrorCode, detail: str, retryable: bool) -> None:
        super().__init__(f"{code.value}: {detail}")
        self.code, self.detail, self.retryable = code, detail[:1000], retryable

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code.value, "retryable": self.retryable, "detail": self.detail}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> StageFailure:
        d = d or {}
        try:
            code = ErrorCode(str(d.get("code")))
        except ValueError:
            code = ErrorCode.INTERNAL
        return cls(code, str(d.get("detail") or ""), bool(d.get("retryable", True)))


def classify(exc: BaseException) -> StageFailure:
    """Map whatever a stage raised to an ErrorCode (AD §16.1). Unknown errors are INTERNAL and retryable."""
    if isinstance(exc, StageFailure):
        return exc
    from cv_core.io.normalize import FFmpegError, MediaError
    if isinstance(exc, MediaError):  # the input itself is bad: same file fails the same way
        return StageFailure(ErrorCode.VIDEO_UNREADABLE, str(exc), retryable=False)
    if isinstance(exc, FFmpegError):
        return StageFailure(ErrorCode.INTERNAL, str(exc), retryable=True)
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code in ErrorCode.__members__:  # stage-defined error carrying a contract code
        return StageFailure(ErrorCode(code), str(exc), retryable=bool(getattr(exc, "retryable", False)))
    if "TeamSeparation" in type(exc).__name__:
        return StageFailure(ErrorCode.TEAM_SEPARATION_FAILED, str(exc), retryable=False)
    return StageFailure(ErrorCode.INTERNAL, f"{type(exc).__name__}: {exc}", retryable=True)
