"""Print the OpenAPI contract: `uv run python -m pitchvision_api.openapi > apps/api/openapi.json`.

apps/web generates its TS client from that file; CI fails if it drifts from the code.
"""
import json
import sys

from pitchvision_api.main import create_app
from pitchvision_api.settings import Settings


def render() -> str:
    return json.dumps(create_app(Settings(env="local")).openapi(), indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    sys.stdout.write(render())
