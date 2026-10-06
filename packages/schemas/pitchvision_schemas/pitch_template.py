"""The FIFA pitch template (32 keypoints + painted lines, metres) for clients — SCR-15 manual calibration.

``pitch_template.json`` is GENERATED from ``cv_core.pitch.template`` (the single source of truth) and committed here,
because neither this package nor the API may import cv_core:

    uv run python -m cv_core.pitch.export_template          # rewrite the JSON
    uv run python -m cv_core.pitch.export_template --check  # CI / tests: fail if it drifted

Coordinates: x along the touchline 0 → length (left goal line → right), y across 0 → width (far touchline → near
touchline, image-down for the main camera). Keypoint ``index`` is what POST /results/{id}/calibration expects as
``template_index``.
"""
import json
from functools import lru_cache
from importlib import resources
from typing import Literal

from pydantic import BaseModel, Field

TEMPLATE_FILE = "pitch_template.json"


class TemplateKeypoint(BaseModel):
    index: int = Field(..., ge=0, le=31)
    name: str = Field(..., description="e.g. corner_tl, l_box_front_far, half_circle_near")
    kind: Literal["intersection", "arc", "spot", "virtual"] = Field(
        ..., description="intersection / arc on the halfway line = precise; spot; virtual = not painted "
        "(extrapolated, noisier); circle_left/right are tangent points (imprecise along x)")
    x: float = Field(..., description="metres")
    y: float = Field(..., description="metres")


class TemplateLine(BaseModel):
    name: str
    kind: Literal["segment", "arc", "spot"]
    closed: bool = False
    points: list[tuple[float, float]] = Field(..., description="polyline in metres")


class PitchTemplateInfo(BaseModel):
    length_m: float
    width_m: float
    keypoints: list[TemplateKeypoint]
    lines: list[TemplateLine]


@lru_cache(maxsize=1)
def load_pitch_template() -> PitchTemplateInfo:
    raw = resources.files("pitchvision_schemas").joinpath(TEMPLATE_FILE).read_text()
    return PitchTemplateInfo.model_validate(json.loads(raw))
