"""Resource limits for the ffmpeg/ffprobe children that parse user files (AD §14 "file độc hại", M1-BE-22).

A crafted file can make a demuxer/decoder allocate without bound or spin; the timeout covers spinning, these
rlimits cover memory, core dumps and stray file writes. The container (non-root, ``no-new-privileges``,
``cap_drop: ALL``, Docker's default seccomp profile — infra/compose) is the outer wall; this is the inner one.

Linux only (``preexec_fn`` → ``setrlimit`` in the child). On macOS ``RLIMIT_AS`` is not enforced, so dev
machines get no limits. ``PITCHVISION_FFMPEG_MAX_MEM_MB=0`` turns the memory cap off (e.g. to debug a big file).
"""

from __future__ import annotations

import os
import resource
import sys
from collections.abc import Callable

PROBE_MAX_MEM_MB = 2048  # header parsing + a few decoded frames for stream info
FFMPEG_MAX_MEM_MB = 8192  # 8K HEVC decode + scale + x264 encode stays well under this


def _env_mb(default: int) -> int:
    raw = os.environ.get("PITCHVISION_FFMPEG_MAX_MEM_MB")
    if raw is None or not raw.strip():
        return default
    return max(0, int(raw))


def child_limits(max_mem_mb: int | None, *, max_file_mb: int | None = None) -> Callable[[], None] | None:
    """``preexec_fn`` applying the limits, or None (non-Linux, or nothing to limit).

    ``max_mem_mb``: address-space cap (None = none; never pass one for CUDA/NVENC children — CUDA reserves
    huge virtual ranges). The env override applies to any non-None cap. ``max_file_mb``: largest file the
    child may write (0 = none at all, for probes).
    """
    if not sys.platform.startswith("linux"):
        return None
    mem = None if max_mem_mb is None else _env_mb(max_mem_mb) or None
    limits: list[tuple[int, int]] = [(resource.RLIMIT_CORE, 0)]
    if mem is not None:
        limits.append((resource.RLIMIT_AS, mem * 1024 * 1024))
    if max_file_mb is not None:
        limits.append((resource.RLIMIT_FSIZE, max_file_mb * 1024 * 1024))

    def apply() -> None:  # runs in the forked child before exec: keep it to syscalls
        for which, value in limits:
            _, hard = resource.getrlimit(which)
            resource.setrlimit(which, (value if hard == resource.RLIM_INFINITY else min(value, hard),) * 2)

    return apply
