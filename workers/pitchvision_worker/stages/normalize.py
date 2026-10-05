"""S2 probe & normalise (AD §6.2, M1-CV-07): ``source/original.*`` → ``source/normalized.mp4`` + manifest fields.

Thin wrapper over ``cv_core.io.normalize``; the task layer downloads the original, calls ``run_normalize`` and
uploads ``normalized.mp4``. Errors: ``cv_core.io.MediaError`` (bad input, do not retry — its ``code`` goes to the
job error) and ``cv_core.io.FFmpegError`` (tool failure / timeout, retryable).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from cv_core.io.normalize import Encoder, NormalizeResult, normalize
from pitchvision_schemas import PipelineConfig

log = logging.getLogger(__name__)

NORMALIZED_NAME = "normalized.mp4"


@dataclass(frozen=True, slots=True)
class NormalizeOutput:
    path: Path                      # local normalized.mp4
    manifest: dict[str, int]        # fps, width, height, duration_ms, frame_count (pitchvision_schemas.Manifest)
    source: dict[str, object]       # original's probe: videos.fps / width / height / duration_ms + codec
    encoder: str
    elapsed_s: float


def run_normalize(original: Path, out_dir: Path, config: PipelineConfig | None = None, *,
                  encoder: Encoder = "auto", timeout_s: float | None = None) -> NormalizeOutput:
    """Probe + validate ``original`` and write ``out_dir/normalized.mp4`` per ``config`` (25 fps, 1280, GOP 2 s)."""
    cfg = config or PipelineConfig()
    res: NormalizeResult = normalize(
        original, Path(out_dir) / NORMALIZED_NAME, fps=cfg.target_fps, long_side=cfg.long_side_px,
        keyframe_interval_s=cfg.keyframe_interval_s, encoder=encoder, timeout_s=timeout_s,
    )
    src = res.source
    log.info("S2 normalise: %s %dx%d@%.3f → %dx%d@%d, %d frames, %.1fs (%.1f× realtime, %s)", original.name,
             src.width, src.height, src.fps, res.output.width, res.output.height, cfg.target_fps,
             res.output.frame_count, res.elapsed_s, res.speed, res.encoder)
    return NormalizeOutput(
        path=res.path, manifest=res.manifest_fields(),
        source={"fps": round(src.fps, 3), "width": src.width, "height": src.height, "duration_ms": src.duration_ms,
                "video_codec": src.video_codec, "container": src.container, "audio_codec": src.audio_codec},
        encoder=res.encoder, elapsed_s=res.elapsed_s,
    )
