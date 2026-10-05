"""Object-store key layout (AD §9.2, revised 05/10/2026).

The storage class comes FIRST because R2 lifecycle rules filter by key prefix only: `source/` and
`segments/` move to Infrequent Access after 30 days and `segments/` is deleted after 180 days
(infra/terraform/modules/r2-region). Everything for one video is still found with `video_prefixes()`.
"""
from typing import Literal

StorageClass = Literal["source", "media", "segments", "results", "tiles", "exports"]
STORAGE_CLASSES: tuple[StorageClass, ...] = ("source", "media", "segments", "results", "tiles", "exports")


def _video(cls: StorageClass, org_id: str, video_id: str) -> str:
    return f"{cls}/orgs/{org_id}/videos/{video_id}/"


def source_key(org_id: str, video_id: str, ext: str) -> str:
    """The user's original upload / YouTube download."""
    return _video("source", org_id, video_id) + f"original.{ext.lstrip('.').lower()}"


def normalized_key(org_id: str, video_id: str) -> str:
    """S2 output: H.264, 25 fps, long side 1280, keyframe every 2 s."""
    return _video("media", org_id, video_id) + "normalized.mp4"


def manifest_key(org_id: str, video_id: str) -> str:
    return _video("media", org_id, video_id) + "manifest.json"


def segment_prefix(org_id: str, video_id: str, job_id: str, segment: int) -> str:
    """S4 raw output (detections/homography/embeddings parquet + _SUCCESS) — expires after 180 days."""
    return _video("segments", org_id, video_id) + f"jobs/{job_id}/{segment:03d}/"


def job_results_prefix(org_id: str, video_id: str, job_id: str) -> str:
    """Durable job artefacts: models/team_model.pkl, tracks/tracks.parquet, stats/…"""
    return _video("results", org_id, video_id) + f"jobs/{job_id}/"


def tile_key(org_id: str, video_id: str, job_id: str, revision: int, tile_no: int) -> str:
    return _video("tiles", org_id, video_id) + f"jobs/{job_id}/r{revision}/{tile_no:06d}.json.gz"


def export_key(org_id: str, video_id: str, export_id: str, ext: str) -> str:
    return _video("exports", org_id, video_id) + f"{export_id}.{ext.lstrip('.').lower()}"


def video_prefixes(org_id: str, video_id: str) -> list[str]:
    """Every prefix holding data for a video — erasure requests delete all of them (AD §14)."""
    return [_video(c, org_id, video_id) for c in STORAGE_CLASSES]
