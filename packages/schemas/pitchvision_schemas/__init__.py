"""Shared contracts (AD §5): pipeline config, GPU/CPU tasks, WS messages, manifest, tiles, shot timeline.

A change here is a contract change: API, workers and the web client must agree on it in the same PR.
"""
from pitchvision_schemas.compute import Capacity, CpuStage, CpuTask, GpuStage, GpuTask, TaskHandle, TaskStatus
from pitchvision_schemas.errors import ErrorCode, Problem
from pitchvision_schemas.jobs import JOB_TRANSITIONS, JobStatus, can_transition
from pitchvision_schemas.keys import shots_key, tile_key
from pitchvision_schemas.manifest import Coverage, Manifest, PitchCalibration, TeamInfo
from pitchvision_schemas.pipeline import PipelineConfig, Segment
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
    "JOB_TRANSITIONS",
    "SCHEMA_VERSION",
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
    "FailedEvent",
    "GpuStage",
    "GpuTask",
    "JobEvent",
    "JobStatus",
    "Manifest",
    "ObjCls",
    "PingMessage",
    "PipelineConfig",
    "PitchCalibration",
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
    "Tile",
    "TileFrame",
    "UnsubscribedMessage",
    "can_transition",
    "shots_key",
    "tile_key",
    "tile_no_for",
]
