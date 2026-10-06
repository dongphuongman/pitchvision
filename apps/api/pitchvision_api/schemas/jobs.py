"""Request/response bodies for /api/v1/jobs (M1-BE-09). Status values and transitions: pitchvision_schemas.jobs."""

import json
import uuid
from datetime import datetime
from typing import Any, Literal

from pitchvision_schemas import JOB_KINDS, ErrorCode, JobKind, JobStatus
from pydantic import BaseModel, Field

from pitchvision_api.db.models import Job


class JobCreate(BaseModel):
    video_id: uuid.UUID
    config: dict[str, Any] | None = Field(
        None,
        description="Partial PipelineConfig (e.g. {\"frame_stride\": 2}); omitted fields take the defaults. "
        "The full config is stored on the job so it can be reproduced.",
    )


class JobError(BaseModel):
    code: ErrorCode
    retryable: bool = Field(description="true → POST /jobs/{id}/retry resumes from the failed stage")
    detail: str | None = None


class SegmentCounts(BaseModel):
    done: int
    running: int
    total: int


class JobCoverage(BaseModel):
    main_wide_pct: float = Field(ge=0, le=100, description="% of the duration in analysable wide shots (S2b)")


PitchCalibrationStatus = Literal["ok", "degraded", "manual", "unavailable"]
PITCH_STATUSES = frozenset(("ok", "degraded", "manual", "unavailable"))


class LatestJob(BaseModel):
    """Summary of a video's newest job, embedded in VideoOut so lists need no per-video job calls."""

    id: uuid.UUID
    status: JobStatus
    progress: float = Field(ge=0, le=100)
    stage: str | None
    error: JobError | None
    coverage: JobCoverage | None
    pitch_calibration: PitchCalibrationStatus | None = Field(
        description="Manifest `pitch.status` of the analysis (`degraded` → \"Low pitch quality\"; `unavailable` → no "
        "pitch keypoint model, so no pitch coordinates / tactical map). null until S5 has run (the orchestrator "
        "writes jobs.detail.pitch_calibration, M1-CV-11/12)."
    )

    @classmethod
    def of(cls, job: Job) -> "LatestJob":
        detail = job.detail or {}
        pitch = detail.get("pitch_calibration")
        return cls(
            id=job.id,
            status=JobStatus(job.status),
            progress=float(job.progress),
            stage=job.stage,
            error=job_error(job.error),
            coverage=JobCoverage.model_validate(job.coverage) if job.coverage else None,
            pitch_calibration=pitch if pitch in PITCH_STATUSES else None,
        )


def _uuid(raw: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(raw)) if raw else None
    except ValueError:
        return None


class JobOut(BaseModel):
    id: uuid.UUID
    video_id: uuid.UUID
    status: JobStatus
    progress: float = Field(ge=0, le=100, description="Weighted over stages; S4 infer is 80 %")
    stage: str | None = Field(
        None, description="Current stage: ingest, normalize, shots, plan, calibrate, infer, stitch, recompute, tiles, "
        "load_db, manifest"
    )
    kind: JobKind = Field("analyze", description="analyze · team_rerun (SCR-14 manual teams: re-runs S3 with the "
                          "user's seeds, then S4…) · recalibrate (SCR-15 manual pitch calibration: CPU re-projection "
                          "+ new tiles revision, no GPU)")
    parent_job_id: uuid.UUID | None = Field(None, description="derived jobs: the job whose finished stages they reuse")
    segments: SegmentCounts | None = Field(None, description="S4 fan-out, once segments are planned")
    fps: float | None = None
    eta_s: int | None = None
    error: JobError | None = None
    coverage: JobCoverage | None = None
    image_tag: str
    config: dict[str, Any] = Field(description="Full PipelineConfig the job runs with")
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def of(cls, job: Job) -> "JobOut":
        detail = job.detail or {}
        return cls(
            id=job.id,
            video_id=job.video_id,
            status=JobStatus(job.status),
            progress=float(job.progress),
            stage=job.stage,
            kind=detail.get("kind") if detail.get("kind") in JOB_KINDS else "analyze",
            parent_job_id=_uuid(detail.get("parent_job_id")),
            segments=SegmentCounts.model_validate(detail["segments"]) if detail.get("segments") else None,
            fps=detail.get("fps"),
            eta_s=detail.get("eta_s"),
            error=job_error(job.error),
            coverage=JobCoverage.model_validate(job.coverage) if job.coverage else None,
            image_tag=job.image_tag,
            config=job.config,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )


def job_error(raw: Any) -> JobError | None:
    """jobs.error is written by the orchestrator: {code, retryable, detail}; tolerate anything else."""
    if not raw:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = {"detail": raw}
    try:
        code = ErrorCode(raw.get("code"))
    except ValueError:
        code = ErrorCode.INTERNAL
    return JobError(code=code, retryable=bool(raw.get("retryable")), detail=raw.get("detail"))


class JobList(BaseModel):
    items: list[JobOut] = Field(description="Newest first")
