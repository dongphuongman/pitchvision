"""Team-assignment eval: accuracy per match (best of the 0/1 and swapped mapping), pooled per stratum.

GT and predictions are ``pv-team-labels/v1`` files (see eval/README.md) joined on (video, frame_idx, obj);
a GT item with no prediction counts as wrong.

    uv run python eval/run_teams.py --gt labels/teams.json --pred out/team_preds.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from pveval.formats import read_team_labels
from pveval.golden import GOLDEN_JSON, STRATA, load_golden
from pveval.metrics import TeamReport, by_group, team_accuracy
from pveval.report import group_tables, table

COLS = ["n", "accuracy"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gt", type=Path, required=True)
    ap.add_argument("--pred", type=Path, required=True)
    ap.add_argument("--golden", type=Path, default=GOLDEN_JSON)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()

    golden = load_golden(a.golden)
    gt, pred = read_team_labels(a.gt), read_team_labels(a.pred)
    per_match: dict[str, TeamReport] = {}
    for vid in sorted({k[0] for k in gt}):
        keys = [k for k in gt if k[0] == vid]
        per_match[vid] = team_accuracy([pred.get(k, -2) for k in keys], [gt[k] for k in keys])

    def pooled(ids: list[str]) -> dict[str, float]:
        n = sum(per_match[i].n for i in ids if i in per_match)
        c = sum(per_match[i].correct for i in ids if i in per_match)
        return {"n": n, "accuracy": c / n if n else float("nan")}

    rows = {v: {"n": r.n, "accuracy": r.accuracy, "swapped": str(r.swapped)} for v, r in per_match.items()}
    print(table("per match", rows, [*COLS, "swapped"]))
    strata = {v: golden.get(v, {}) for v in per_match}
    tables = by_group(strata, STRATA, pooled)
    print("\n" + group_tables(tables, COLS))
    overall = pooled(list(per_match))
    print("\n" + table("ALL", {"all": overall}, COLS))
    if a.out:
        a.out.write_text(json.dumps({"overall": overall, "by_group": {t.key: t.rows for t in tables}}, indent=1))


if __name__ == "__main__":
    main()
