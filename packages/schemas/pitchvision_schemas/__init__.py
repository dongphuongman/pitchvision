"""Shared contracts (AD §5): pipeline config, GPU/CPU tasks, WS messages, manifest, tiles, shot timeline.

A change here is a contract change: API, workers and the web client must agree on it in the same PR.
"""
from pitchvision_schemas.compute import Capacity, CpuStage, CpuTask, GpuStage, GpuTask, TaskHandle, TaskStatus
from pitchvision_schemas.errors import ErrorCode, Problem
from pitchvision_schemas.exports import (
    EXPORT_HEIGHTS,
    TERMINAL_EXPORT,
    ExportKind,
    ExportLayers,
    ExportLayout,
    ExportSpec,
    ExportStatus,
)
from pitchvision_schemas.jobs import JOB_KINDS, JOB_TRANSITIONS, JobKind, JobStatus, can_transition
from pitchvision_schemas.keys import shots_key, tile_key
from pitchvision_schemas.manifest import Coverage, Manifest, PitchCalibration, PitchStatus, TeamInfo
from pitchvision_schemas.pipeline import PipelineConfig, Segment
from pitchvision_schemas.pitch_template import (
    PitchTemplateInfo,
    TemplateKeypoint,
    TemplateLine,
    load_pitch_template,
)
from pitchvision_schemas.plans import PLAN_LIMITS, Plan, PlanLimits, limits_for, plan_of
from pitchvision_schemas.shots import Shot, ShotTimeline, ShotType
from pitchvision_schemas.tiles import TILE_MS, ObjCls, Tile, TileFrame, tile_no_for
from pitchvision_schemas.ws import (
    CancelledEvent,
    ClientOp,
    CompletedEvent,
    ControlMessage,
    ErrorMessage,
    FailedEvent,
    JobEvent,
    PingMessage,
    PongMessage,
    ProgressData,
    ProgressEvent,
    ServerEvent,
    SubscribedMessage,
    UnsubscribedMessage,
)

SCHEMA_VERSION = 1

__all__ = [
    "EXPORT_HEIGHTS",
    "JOB_KINDS",
    "JOB_TRANSITIONS",
    "PLAN_LIMITS",
    "SCHEMA_VERSION",
    "TERMINAL_EXPORT",
    "TILE_MS",
    "CancelledEvent",
    "Capacity",
    "ClientOp",
    "CompletedEvent",
    "ControlMessage",
    "Coverage",
    "CpuStage",
    "CpuTask",
    "ErrorCode",
    "ErrorMessage",
    "ExportKind",
    "ExportLayers",
    "ExportLayout",
    "ExportSpec",
    "ExportStatus",
    "FailedEvent",
    "GpuStage",
    "GpuTask",
    "JobEvent",
    "JobKind",
    "JobStatus",
    "Manifest",
    "ObjCls",
    "PingMessage",
    "PipelineConfig",
    "PitchCalibration",
    "PitchStatus",
    "PitchTemplateInfo",
    "Plan",
    "PlanLimits",
    "PongMessage",
    "Problem",
    "ProgressData",
    "ProgressEvent",
    "Segment",
    "ServerEvent",
    "Shot",
    "ShotTimeline",
    "ShotType",
    "SubscribedMessage",
    "TaskHandle",
    "TaskStatus",
    "TeamInfo",
    "TemplateKeypoint",
    "TemplateLine",
    "Tile",
    "TileFrame",
    "UnsubscribedMessage",
    "can_transition",
    "limits_for",
    "load_pitch_template",
    "plan_of",
    "shots_key",
    "tile_key",
    "tile_no_for",
]
