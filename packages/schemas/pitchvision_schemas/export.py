"""Dump JSON Schema for the browser-facing contracts → packages/schemas/jsonschema/ (web generates TS types).

    uv run python -m pitchvision_schemas.export          # write
    uv run python -m pitchvision_schemas.export --check  # CI: fail if out of date
"""
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from pitchvision_schemas import ClientOp, Manifest, ServerEvent, ShotTimeline, Tile

OUT = Path(__file__).resolve().parents[1] / "jsonschema"
TARGETS: dict[str, TypeAdapter[Any]] = {
    "manifest": TypeAdapter(Manifest),
    "tile": TypeAdapter(Tile),
    "shot_timeline": TypeAdapter(ShotTimeline),
    "ws_server_event": TypeAdapter(ServerEvent),
    "ws_client_op": TypeAdapter(ClientOp),
}


def _draft7_tuples(node: Any) -> Any:
    """prefixItems (2020-12) → items-array tuples (draft-07) so json-schema-to-typescript emits TS tuples."""
    if isinstance(node, dict):
        node = {k: _draft7_tuples(v) for k, v in node.items()}
        if "prefixItems" in node:
            node["items"] = node.pop("prefixItems")
            node.setdefault("additionalItems", False)
        return node
    if isinstance(node, list):
        return [_draft7_tuples(v) for v in node]
    return node


def render() -> dict[str, str]:
    out = {}
    for n, t in TARGETS.items():
        schema = _draft7_tuples(t.json_schema(mode="serialization"))
        out[f"{n}.schema.json"] = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    return out


def main() -> int:
    files = render()
    if "--check" in sys.argv:
        stale = [n for n, s in files.items() if not (OUT / n).exists() or (OUT / n).read_text() != s]
        if stale:
            print("JSON Schema out of date, run `uv run python -m pitchvision_schemas.export`:", *stale)
            return 1
        return 0
    OUT.mkdir(exist_ok=True)
    for n, s in files.items():
        (OUT / n).write_text(s)
    print(f"wrote {len(files)} schemas to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
