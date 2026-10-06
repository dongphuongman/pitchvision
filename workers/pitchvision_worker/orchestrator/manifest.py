"""Result manifest (M1-BE-15): built by the orchestrator from the job's stage results, written LAST.

``keys.manifest_key(org, video)`` gets the manifest only after S7 tiles and S8 load-db are done, immediately before
the job turns ``done`` (same transaction step): a reader that finds a manifest finds every tile it lists. Its
``tile_url_template`` is the OBJECT-KEY template (``keys.tile_prefix(…) + "{tile_no}.json.gz"``); the API swaps in a
signed URL per request (``GET /api/v1/results/{video}/manifest``), so the stored file holds no credential.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping
from typing import Any

from pitchvision_schemas import Coverage, Manifest, PipelineConfig, PitchCalibration, ShotTimeline, TeamInfo, keys

from pitchvision_worker.stages.load_db import team_rows

TILE_TEMPLATE_SUFFIX = "{tile_no}.json.gz"


def build_manifest(*, org_id: uuid.UUID, video_id: uuid.UUID, job_id: uuid.UUID, config: PipelineConfig,
                   media: Mapping[str, Any], timeline: ShotTimeline, team_model: Mapping[str, Any] | None,
                   stitch: Mapping[str, Any], tiles: Mapping[str, Any]) -> Manifest:  # fmt: skip
    """``media`` = the S2 normalize result manifest (fps, width, height, duration_ms, frame_count); ``stitch`` /
    ``tiles`` = those tasks' result payloads (pitch summary; tile_count + revision)."""
    fps = round(float(media.get("fps") or config.target_fps))
    duration_ms = int(media.get("duration_ms") or (timeline.shots[-1].t1_ms if timeline.shots else 0))
    frame_count = int(media.get("frame_count") or math.ceil(duration_ms * fps / 1000))
    revision = int(tiles.get("revision") or 0)
    pitch = stitch.get("pitch") or {}
    status = pitch.get("status") if pitch.get("status") in ("ok", "degraded", "unavailable") else "unavailable"
    return Manifest(
        video_id=str(video_id),
        job_id=str(job_id),
        revision=revision,
        fps=fps,
        width=int(media.get("width") or 0),
        height=int(media.get("height") or 0),
        duration_ms=duration_ms,
        frame_count=frame_count,
        tile_count=int(tiles["tile_count"]),
        tile_url_template=keys.tile_prefix(str(org_id), str(video_id), str(job_id), revision) + TILE_TEMPLATE_SUFFIX,
        shots=list(timeline.shots),
        coverage=Coverage(main_wide_pct=round(100 * timeline.coverage(), 2),
                          homography_ok_pct=float(pitch.get("homography_ok_pct") or 0.0)),
        teams=[TeamInfo(cluster=t.cluster, name=t.name, color=t.color) for t in team_rows(team_model)],
        pitch=PitchCalibration(status=status),
    )
