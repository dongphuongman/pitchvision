"""Shot classification eval (S2b) on the golden set against the SP-09 labels.

Converts SP-09 labels to ``pv-shot-samples/v1`` (out/shot_labels.json), then scores either a predictions
file (``--pred``, samples or ShotTimeline format) or the built-in green-ratio baseline, printing
accuracy + main_wide precision/recall per match and per stratum (AD §18).

    uv run python eval/run_shots.py                         # baseline on SP-09 JPEG frames
    uv run python eval/run_shots.py --frames video          # baseline via cv_core VideoReader on raw/*.mp4
    uv run python eval/run_shots.py --pred my_preds.json    # score a classifier
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from pveval.formats import ShotSample, read_shot_predictions, write_shot_samples
from pveval.golden import GOLDEN_JSON, REPO, STRATA, default_golden_dir, load_golden
from pveval.metrics import EMPTY_SHOTS, ShotReport, by_group, shot_report
from pveval.report import group_tables, table
from pveval.shots import colour_features, frames_from_jpgs, frames_from_videos, green_rule, sp09_samples

COLS = ["n", "accuracy", "wide_precision", "wide_recall"]


def _row(r: ShotReport) -> dict[str, float]:
    return {"n": r.n, "accuracy": r.accuracy, "wide_precision": r.wide_precision, "wide_recall": r.wide_recall}


def run_baseline(samples: list[ShotSample], frames: str, sp09_dir: Path, golden_dir: Path,
                 files: dict[tuple[str, int], str]) -> list[ShotSample]:
    src = (frames_from_jpgs(sp09_dir, files, samples) if frames == "sp09"
           else frames_from_videos(golden_dir, samples))
    preds, t0 = [], time.perf_counter()
    for i, (s, img) in enumerate(src, 1):
        if img is not None:
            label, conf = green_rule(colour_features(img))
            preds.append(ShotSample(s.video, s.t_ms, label, conf))
        if i % 200 == 0:
            print(f"  {i}/{len(samples)} frames ({time.perf_counter() - t0:.0f}s)", file=sys.stderr)
    return preds


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--golden", type=Path, default=GOLDEN_JSON)
    ap.add_argument("--sp09-dir", type=Path,
                    default=Path(os.environ.get("PITCHVISION_SP09_DIR", REPO / "spikes" / "results" / "sp09")))
    ap.add_argument("--golden-dir", type=Path, default=default_golden_dir())
    ap.add_argument("--frames", choices=["auto", "sp09", "video"], default="auto",
                    help="baseline frame source: SP-09 JPEGs or decode raw videos with cv_core")
    ap.add_argument("--pred", type=Path, help="predictions file; omit to run the green-ratio baseline")
    ap.add_argument("--split", choices=["all", "dev", "test"], default="all")
    ap.add_argument("--out-dir", type=Path, default=Path(__file__).parent / "out")
    a = ap.parse_args()

    golden = load_golden(a.golden)
    samples, files = sp09_samples(a.sp09_dir)
    samples = [s for s in samples if s.video in golden and a.split in ("all", golden[s.video]["split"])]
    write_shot_samples(a.out_dir / "shot_labels.json", samples, source="SP-09 labels_nonwide.json",
                       vocabulary=["main_wide", "non_wide"])

    if a.pred is None:
        frames = a.frames
        if frames == "auto":
            frames = "sp09" if (a.sp09_dir / "frames").is_dir() else "video"
        print(f"baseline: green-ratio rule on {len(samples)} frames ({frames})", file=sys.stderr)
        preds = run_baseline(samples, frames, a.sp09_dir, a.golden_dir, files)
        a.pred = a.out_dir / f"shot_preds_green_{frames}.json"
        write_shot_samples(a.pred, preds, model="green-ratio rule (lower 2/3 grass > 0.55)")
    pred = read_shot_predictions(a.pred, samples)

    per_match: dict[str, ShotReport] = {}
    for vid in sorted({s.video for s in samples}):
        mine = [s for s in samples if s.video == vid]
        per_match[vid] = shot_report([pred[(s.video, s.t_ms)] for s in mine], [s.label for s in mine])
    total = sum(per_match.values(), EMPTY_SHOTS)

    def pooled(ids: list[str]) -> dict[str, float]:
        return _row(sum((per_match[i] for i in ids if i in per_match), EMPTY_SHOTS))

    strata = {i: g for i, g in golden.items() if i in per_match}
    rows = {f"{i}  {golden[i]['category'][:5]:5} {golden[i]['split']}": _row(r) for i, r in per_match.items()}
    print(table("per match", rows, COLS))
    print()
    print(group_tables(by_group(strata, STRATA, pooled), COLS))
    print()
    print(table("ALL", {"all": _row(total)}, COLS))
    out = {"pred": str(a.pred), "overall": _row(total), "per_match": {k: _row(v) for k, v in per_match.items()},
           "by_group": {t.key: t.rows for t in by_group(strata, STRATA, pooled)}}
    (a.out_dir / "shots_report.json").write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()
