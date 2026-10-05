"""Regional FastAPI service (AD §5): videos, jobs, results, exports. Depends on pitchvision_schemas only."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pitchvision-api")
except PackageNotFoundError:  # pragma: no cover - source checkout without install
    __version__ = "0.0.0"
