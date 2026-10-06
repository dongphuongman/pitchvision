"""Export the pitch template (keypoints + lines) to ``packages/schemas/pitchvision_schemas/pitch_template.json``.

The API serves it at ``GET /api/v1/results/pitch-template`` (SCR-15) but may not import cv_core, so the JSON is
generated here and committed:

    uv run python -m cv_core.pitch.export_template          # write
    uv run python -m cv_core.pitch.export_template --check  # exit 1 if the committed file drifted
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from cv_core.pitch.template import KEYPOINT_KINDS, KEYPOINT_NAMES, FIFA_105x68, PitchTemplate

OUT = Path(__file__).resolve().parents[3] / "schemas" / "pitchvision_schemas" / "pitch_template.json"
LINE_STEP_M = 1.0


def _r(v: float) -> float:
    return round(float(v), 3) + 0.0  # + 0.0: no "-0.0" in the JSON


def template_dict(template: PitchTemplate = FIFA_105x68) -> dict[str, Any]:
    kp = template.keypoints
    return {
        "length_m": template.length,
        "width_m": template.width,
        "keypoints": [{"index": i, "name": KEYPOINT_NAMES[i], "kind": KEYPOINT_KINDS[i], "x": _r(kp[i, 0]),
                       "y": _r(kp[i, 1])} for i in range(len(kp))],
        "lines": [{"name": ln.name, "kind": ln.kind, "closed": ln.closed,
                   "points": [[_r(x), _r(y)] for x, y in ln.points]} for ln in template.lines(LINE_STEP_M)],
    }


def render() -> str:
    return json.dumps(template_dict(), separators=(",", ":")) + "\n"


def main() -> int:
    text = render()
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text() != text:
            print(f"{OUT} is out of date, run `uv run python -m cv_core.pitch.export_template`")
            return 1
        return 0
    OUT.write_text(text)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
