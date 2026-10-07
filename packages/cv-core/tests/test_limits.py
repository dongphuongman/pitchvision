"""cv_core.io.limits: rlimits for ffmpeg/ffprobe children (M1-BE-22)."""

import resource
import subprocess
import sys

import pytest
from cv_core.io import limits
from cv_core.io.limits import child_limits

MB = 1024 * 1024


def test_no_limits_off_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits.sys, "platform", "darwin")
    assert child_limits(512, max_file_mb=0) is None


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="rlimits are applied on Linux only")
def test_child_gets_the_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PITCHVISION_FFMPEG_MAX_MEM_MB", raising=False)
    show = (
        "import resource as r; print(r.getrlimit(r.RLIMIT_AS)[0], r.getrlimit(r.RLIMIT_CORE)[0], "
        "r.getrlimit(r.RLIMIT_FSIZE)[0])"
    )
    out = subprocess.run(
        [sys.executable, "-c", show],
        capture_output=True,
        text=True,
        check=True,
        preexec_fn=child_limits(1024, max_file_mb=0),
    ).stdout.split()
    assert [int(v) for v in out] == [1024 * MB, 0, 0]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="rlimits are applied on Linux only")
def test_memory_cap_stops_a_runaway_allocation() -> None:
    hog = "b = bytearray(1500 * 1024 * 1024); print('allocated')"
    proc = subprocess.run([sys.executable, "-c", hog], capture_output=True, text=True, preexec_fn=child_limits(1024))
    assert proc.returncode != 0 and "MemoryError" in proc.stderr


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="rlimits are applied on Linux only")
def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    show = "import resource as r; print(r.getrlimit(r.RLIMIT_AS)[0])"
    monkeypatch.setenv("PITCHVISION_FFMPEG_MAX_MEM_MB", "0")  # off
    out = subprocess.run(
        [sys.executable, "-c", show], capture_output=True, text=True, check=True, preexec_fn=child_limits(1024)
    ).stdout
    assert int(out) == resource.getrlimit(resource.RLIMIT_AS)[0]
    monkeypatch.setenv("PITCHVISION_FFMPEG_MAX_MEM_MB", "3000")
    out = subprocess.run(
        [sys.executable, "-c", show], capture_output=True, text=True, check=True, preexec_fn=child_limits(1024)
    ).stdout
    assert int(out) == 3000 * MB
