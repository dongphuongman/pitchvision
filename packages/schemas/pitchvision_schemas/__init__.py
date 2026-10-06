"""Shared contracts (AD §5): pipeline config, GPU tasks, WS messages, manifest, tiles, shot timeline.

A change here is a contract change: API, workers and the web client must agree on it in the same PR.
"""
from pitchvision_schemas.compute import Capacity, GpuStage, GpuTask, TaskHandle, TaskStatus
from pitchvision_schemas.errors import ErrorCode, Problem
from pitchvision_schemas.jobs import JOB_TRANSITIONS, JobStatus, can_transition
from pitchvision_schemas.keys import tile_key
from pitchvision_schemas.manifest import Coverage, Manifest, PitchCalibration, TeamInfo
from pitchvision_schemas.pipeline import PipelineConfig, Segment
from pitchvision_schemas.shots import Shot, ShotTimeline, ShotType
from pitchvision_schemas.tiles import TILE_MS, ObjCls, Tile, TileFrame, tile_no_for
from pitchvision_schemas.ws import ClientOp, CompletedEvent, FailedEvent, ProgressData, ProgressEvent, ServerEvent

SCHEMA_VERSION = 1

__all__ = [
    "JOB_TRANSITIONS",
    "SCHEMA_VERSION",
    "TILE_MS",
    "Capacity",
    "ClientOp",
    "CompletedEvent",
    "Coverage",
    "ErrorCode",
    "FailedEvent",
    "GpuStage",
    "GpuTask",
    "JobStatus",
    "Manifest",
    "ObjCls",
    "PipelineConfig",
    "PitchCalibration",
    "Problem",
    "ProgressData",
    "ProgressEvent",
    "Segment",
    "ServerEvent",
    "Shot",
    "ShotTimeline",
    "ShotType",
    "TaskHandle",
    "TaskStatus",
    "TeamInfo",
    "Tile",
    "TileFrame",
    "can_transition",
    "tile_key",
    "tile_no_for",
]
