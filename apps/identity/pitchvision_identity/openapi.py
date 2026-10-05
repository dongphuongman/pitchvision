"""Print the OpenAPI contract: `uv run python -m pitchvision_identity.openapi > apps/identity/openapi.json`.

apps/web generates its identity client from that file; a test fails if it drifts from the code.
"""

import json
import logging
import sys

from pitchvision_identity.main import create_app
from pitchvision_identity.settings import Settings


def render() -> str:
    logging.getLogger("pitchvision_identity.security").setLevel(logging.ERROR)  # ephemeral-key warning is noise here
    return json.dumps(create_app(Settings(env="test")).openapi(), indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    sys.stdout.write(render())
