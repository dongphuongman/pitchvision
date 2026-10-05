"""Shot classification eval (S2b) on the golden set against the SP-09 labels.

Converts SP-09 labels to ``pv-shot-samples/v1`` (out/shot_labels.json), then scores either a predictions
file (``--pred``, samples or ShotTimeline format) or the built-in green-ratio baseline, printing
accuracy + main_wide precision/recall per match and per stratum (AD §18).

    uv run python eval/run_shots.py                         # baseline on SP-09 JPEG frames
    uv run python eval/run_shots.py --frames video          # baseline via cv_core VideoReader on raw/*.mp4
    uv run python eval/run_shots.py --pred my_preds.json    # score a classifier
    uv run python eval/run_shots.py --timeline-models training/out/folds_det --jobs 3   # full S2b on raw videos

``--timeline-models DIR``: runs ``cv_core.shots.segment_video`` (cuts + per-second classification + smoothing →
ShotTimeline) over every labelled golden video with ``DIR/<video>.json`` — the leave-that-video-out model written by
``training/train_shots.py --export-folds`` — and scores the timelines at the GT timestamps (pv-shot-timelines/v1).
``--report`` sets where the JSON summary goes (default ``<out-dir>/shots_report.json``).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

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


def _segment_one(video: str, raw: str, model_path: str, detector: str | None, provider: str) -> dict[str, Any]:
    """Worker: full S2b on one raw video (VideoReader resamples to 25 fps like normalized.mp4)."""
    from cv_core.detect import DFineDetector
    from cv_core.runtime import Runtime
    from cv_core.shots import GbmModel, ShotClassifier, segment_video

    model = GbmModel.load(model_path)
    det = None
    if model.detector:
        if detector is None:
            raise SystemExit(f"{model_path} needs a detector (--detector)")
        det = DFineDetector(Runtime.select(prefer=provider).model(detector), conf=0.35)
    t0 = time.perf_counter()
    res = segment_video(raw, ShotClassifier(model, det))
    wall = time.perf_counter() - t0
    print(f"  {video}: {res.frames} frames, {len(res.cuts_ms)} cuts, {len(res.timeline.shots)} shots, "
          f"{wall:.0f}s ({res.frames / wall:.0f} fps)", file=sys.stderr)
    return {"video": video, "timeline": res.timeline.model_dump(mode="json"), "frames": res.frames,
            "cuts": len(res.cuts_ms), "wall_s": round(wall, 1),
            "timings_s": {k: round(v, 1) for k, v in res.timings_s.items()}}


def run_timelines(videos: list[str], golden_dir: Path, models: Path, detector: Path | None, provider: str,
                  jobs: int, out: Path) -> Path:
    todo = [(v, str(golden_dir / "raw" / f"{v}.mp4"), str(models / f"{v}.json")) for v in videos
            if (golden_dir / "raw" / f"{v}.mp4").is_file() and (models / f"{v}.json").is_file()]
    print(f"S2b timelines on {len(todo)} videos ({jobs} jobs)", file=sys.stderr)
    det = str(detector) if detector else None
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        results = list(ex.map(_segment_one, *zip(*todo, strict=True), [det] * len(todo), [provider] * len(todo)))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"format": "pv-shot-timelines/v1", "models": str(models),
                               "timelines": {r["video"]: r["timeline"] for r in results},
                               "runs": {r["video"]: {k: r[k] for k in ("frames", "cuts", "wall_s", "timings_s")}
                                        for r in results}}) + "\n")
    return out


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
    ap.add_argument("--report", type=Path, help="summary JSON path (default <out-dir>/shots_report.json)")
    ap.add_argument("--timeline-models", type=Path, help="dir of per-video leave-out pv-gbm models → full S2b run")
    ap.add_argument("--detector", type=Path,
                    default=Path(os.environ.get("PITCHVISION_MODEL_DIR", Path.home() / ".cache" / "pitchvision"
                                                / "models")) / "dfine-m-obj2coco-640.onnx")
    ap.add_argument("--provider", default="ort_coreml", help="Runtime provider preference for the detector")
    ap.add_argument("--jobs", type=int, default=2)
    a = ap.parse_args()

    golden = load_golden(a.golden)
    samples, files = sp09_samples(a.sp09_dir)
    samples = [s for s in samples if s.video in golden and a.split in ("all", golden[s.video]["split"])]
    write_shot_samples(a.out_dir / "shot_labels.json", samples, source="SP-09 labels_nonwide.json",
                       vocabulary=["main_wide", "non_wide"])

    meta: dict[str, Any] = {}
    if a.timeline_models is not None:
        a.pred = run_timelines(sorted({s.video for s in samples}), a.golden_dir, a.timeline_models, a.detector,
                               a.provider, a.jobs, a.out_dir / f"shot_timelines_{a.timeline_models.name}.json")
        runs = json.loads(a.pred.read_text())["runs"]
        frames_total = sum(r["frames"] for r in runs.values())
        meta["s2b_runs"] = {"videos": len(runs), "frames": frames_total,
                            "wall_s_sum": round(sum(r["wall_s"] for r in runs.values()), 1),
                            "fps_per_process": round(frames_total / max(sum(r["wall_s"] for r in runs.values()), 1e-9),
                                                     1),
                            "jobs": a.jobs, "per_video": runs}
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
           "by_group": {t.key: t.rows for t in by_group(strata, STRATA, pooled)}, **meta}
    report = a.report or a.out_dir / "shots_report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()
