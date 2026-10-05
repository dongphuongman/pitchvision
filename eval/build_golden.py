"""(Re)build eval/golden.json from data/golden/manifest.json.

    uv run python eval/build_golden.py [--manifest data/golden/manifest.json] [--out eval/golden.json]
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from pveval.golden import GOLDEN_JSON, REPO, STRATA, build_golden


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", type=Path, default=REPO / "data" / "golden" / "manifest.json")
    ap.add_argument("--out", type=Path, default=GOLDEN_JSON)
    a = ap.parse_args()
    golden = build_golden(json.loads(a.manifest.read_text()))
    a.out.write_text(json.dumps(golden, indent=2, ensure_ascii=False) + "\n")
    ms = golden["matches"]
    print(f"{len(ms)} matches → {a.out} ({len(golden['missing'])} missing)")
    for k in STRATA:
        print(f"  {k:15} {dict(sorted(Counter(str(m[k]) for m in ms).items()))}")


if __name__ == "__main__":
    main()
