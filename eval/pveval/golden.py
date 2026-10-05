"""Golden set v0 description (eval/golden.json): one entry per match with stratification fields.

Fields per match: id, match, competition, channel, category (men/women/youth), gender, age_group, region,
camera_source, format, fps, fps_class, width, height, resolution, source_quality, lighting
(day/night/null = unknown), kit_similarity (null until labelled), hard_cases, split (dev/test).
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parents[1]
REPO = EVAL_DIR.parent
GOLDEN_JSON = EVAL_DIR / "golden.json"
STRATA = ("category", "region", "fps_class", "lighting", "source_quality", "split")
TEST_SHARE = 0.4

# Curated from data/golden/README.md and segments.txt (venue region; README "hard cases").
REGION = {
    "BukNW8GRI5A": "north_america", "ZKLKNUMtL6w": "north_america", "TyCCa3tyhZw": "north_america",
    "A4xKBpFV9Cw": "asia", "TAI1eZYx5Kg": "oceania", "WQUD7YPsOho": "asia", "wyKftTv03Ag": "asia",
    "EhqJ8-g1YvU": "europe", "UKFYHHQzTvM": "europe", "caZwnRXhkAk": "europe", "Ido_K83gIs4": "north_america",
    "MZjaWGwbtEc": "europe", "zQqjHc_ZGcc": "asia", "btFTQmt1Dgo": "europe", "aSq-QJh-5UA": "north_america",
    "_DfZB1Y-E_k": "europe", "EYj9Wbo0n44": "north_america", "AZd7qfwQtfM": "south_america",
    "jYQL469dLjY": "asia", "_FPE6jjJQ8M": "europe",
}
LIGHTING = {  # only what the README states; everything else unknown (null)
    "Ido_K83gIs4": "night", "btFTQmt1Dgo": "night", "WQUD7YPsOho": "night",
    "UKFYHHQzTvM": "day", "_FPE6jjJQ8M": "day",
}
HARD_CASES = {
    "UKFYHHQzTvM": ["sun_shadow"], "_FPE6jjJQ8M": ["sun_shadow"],
    "Ido_K83gIs4": ["floodlights"], "btFTQmt1Dgo": ["floodlights"], "WQUD7YPsOho": ["floodlights"],
    "EYj9Wbo0n44": ["single_camera_youth"],
    "AZd7qfwQtfM": ["sd_4x3_sidebars", "watermark"], "EhqJ8-g1YvU": ["watermark"],
    "TyCCa3tyhZw": ["frequent_replays"], "ZKLKNUMtL6w": ["frequent_replays"], "wyKftTv03Ag": ["frequent_replays"],
}
SD_UPSCALE = {"AZd7qfwQtfM"}


def default_golden_dir() -> Path:
    """``$PITCHVISION_GOLDEN_DIR`` or ``<repo>/data/golden`` (raw videos are gitignored)."""
    return Path(os.environ.get("PITCHVISION_GOLDEN_DIR", REPO / "data" / "golden"))


def _fps_class(fps: float) -> str:
    return str(min((25, 30, 50, 60), key=lambda f: abs(f - fps)))


def _split(ids_by_cat: Mapping[str, Sequence[str]]) -> dict[str, str]:
    """Deterministic stratified split: per category, ids ordered by sha1, first TEST_SHARE → test."""
    out = {}
    for ids in ids_by_cat.values():
        ordered = sorted(ids, key=lambda i: hashlib.sha1(i.encode()).hexdigest())
        n_test = round(len(ordered) * TEST_SHARE)
        out.update({i: ("test" if r < n_test else "dev") for r, i in enumerate(ordered)})
    return out


def build_golden(manifest: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """golden.json content from data/golden/manifest.json (only downloaded matches are in the set)."""
    have = [m for m in manifest if m.get("downloaded")]
    by_cat: dict[str, list[str]] = {}
    for m in have:
        by_cat.setdefault(m["category"], []).append(m["id"])
    split = _split(by_cat)
    matches = []
    for m in have:
        mid = m["id"]
        matches.append({
            "id": mid,
            "match": m["match"],
            "competition": m["match"].split(" (")[0],
            "channel": m["channel"],
            "category": m["category"],
            "gender": "men" if m["category"] == "men" else "women" if m["category"] == "women" else None,
            "age_group": "youth" if m["category"] == "youth" else "senior",
            "region": REGION.get(mid),
            "camera_source": "broadcast",
            "format": "11v11",
            "fps": m["fps"],
            "fps_class": _fps_class(m["fps"]),
            "width": m["width"],
            "height": m["height"],
            "resolution": f"{m['height']}p",
            "source_quality": "sd_upscale" if mid in SD_UPSCALE else "hd",
            "lighting": LIGHTING.get(mid),
            "kit_similarity": None,
            "hard_cases": HARD_CASES.get(mid, []),
            "duration_s": m["duration_s"],
            "segment_start_s": m["segment_start_s"],
            "file": m["file"],
            "split": split[mid],
        })
    missing = [{"id": m["id"], "match": m["match"], "reason": "not downloaded (YouTube bot-check)"}
               for m in manifest if not m.get("downloaded")]
    return {
        "version": 0,
        "description": "PitchVision golden set v0 — broadcast, 15-min in-play segments (SP-01). Internal R&D only.",
        "generated_by": "eval/build_golden.py from data/golden/manifest.json",
        "strata": list(STRATA),
        "matches": matches,
        "missing": missing,
    }


def load_golden(path: Path = GOLDEN_JSON) -> dict[str, dict[str, Any]]:
    """{match id: entry} from golden.json."""
    data = json.loads(Path(path).read_text())
    return {m["id"]: m for m in data["matches"]}
