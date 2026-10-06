"""W3 pipeline smoke eval (M1-CV-08/09/10): S2 normalise → S2b shots → segment plan → S3 calibrate → S4 infer on the
first minutes of golden matches, with the real stage functions and models.

    uv run python eval/run_pipeline_w3.py --videos MZjaWGwbtEc TyCCa3tyhZw BukNW8GRI5A --minutes 5 \
        --out-dir /tmp/pv_w3 --provider ort_coreml --report eval/results/pipeline_m1cv10_w3.json

Per video it reports: segments, main_wide coverage, the team split (sizes, silhouette, outliers) + crop grids
(``team_samples.jpg`` from S3, ``track_teams.jpg`` = S4 track crops per voted team) for eyeballing, tracking
fragmentation (unique track ids / median visible players, per 90 s — SP-05's ByteTrack baseline was 22.9 and
23.9 on MZjaWGwbtEc / TyCCa3tyhZw) and wall time per stage. Outputs (videos, parquet, grids) stay in ``--out-dir``,
which must be outside the repo; only the small JSON summary is meant to be committed.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from cv_core.detect import DFineDetector
from cv_core.io.parquet import read_detections
from cv_core.io.video import VideoReader
from cv_core.registry import ModelRegistry
from cv_core.runtime import Runtime
from cv_core.shots import GbmModel, ShotClassifier
from cv_core.teams.calibrate import crops_grid
from cv_core.teams.embed import torso_crop
from pitchvision_schemas import PipelineConfig
from pitchvision_worker.stages.calibrate import run_calibrate
from pitchvision_worker.stages.infer import LOW_CONF, run_infer
from pitchvision_worker.stages.models import detector_model_name, load_embedder
from pitchvision_worker.stages.normalize import run_normalize
from pitchvision_worker.stages.segments import plan_segments
from pitchvision_worker.stages.shots import SHOTS_MODEL, run_shots_detailed

ROOT = Path(__file__).resolve().parents[1]
SP05_BASELINE = {"MZjaWGwbtEc": 22.9, "TyCCa3tyhZw": 23.9}  # spikes/results/sp05/summary_bytetrack.json


def golden_dir() -> Path:
    return Path(os.environ.get("PITCHVISION_GOLDEN_DIR", ROOT / "data" / "golden"))


def cut(src: Path, dst: Path, seconds: int) -> None:
    if dst.exists():
        return
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-t", str(seconds), "-map", "0:v:0", "-map",
                    "0:a:0?", "-c", "copy", str(dst)], check=True, timeout=600)


def track_team_grid(normalized: Path, parquet: Path, out: Path, per_team: int = 16) -> dict[str, Any]:
    """Crops of S4 tracks (one per track, its median-time row) per voted team → a grid to eyeball the vote."""
    tab = read_detections(parquet)
    cols = {k: tab.column(k).to_numpy() for k in ("frame_idx", "track_id", "cls", "team", "x1", "y1", "x2", "y2")}
    players = cols["cls"] == 0
    picks: dict[int, list[tuple[int, np.ndarray]]] = {0: [], 1: [], -1: []}
    rng = np.random.default_rng(0)
    tids = np.unique(cols["track_id"][players])
    rng.shuffle(tids)
    for tid in tids:
        idx = np.flatnonzero(players & (cols["track_id"] == tid))
        if len(idx) < 10:
            continue
        team = int(cols["team"][idx[0]])
        if len(picks[team]) >= per_team:
            continue
        r = idx[len(idx) // 2]
        picks[team].append((int(cols["frame_idx"][r]), np.array([cols[k][r] for k in ("x1", "y1", "x2", "y2")])))
    want = sorted({f for v in picks.values() for f, _ in v})
    images: dict[int, np.ndarray] = {}
    for f in want:
        for fr in VideoReader(normalized, f / 25, (f + 1) / 25, target_fps=25):
            images[fr.idx] = fr.image
    rows = []
    for team, name in ((0, "team 0"), (1, "team 1"), (-1, "no team")):
        crops = [c for f, b in picks[team] if f in images and (c := torso_crop(images[f], b)) is not None]
        rows.append((name, crops))
    cv2.imwrite(str(out), crops_grid(rows), [cv2.IMWRITE_JPEG_QUALITY, 90])
    return {k: len(v) for k, v in picks.items()}


def run_video(vid: str, a: argparse.Namespace, rt: Runtime, emb_rt: Runtime | None) -> dict[str, Any]:
    cfg = PipelineConfig()
    work = a.out_dir / vid
    work.mkdir(parents=True, exist_ok=True)
    res: dict[str, Any] = {"video": vid, "minutes": a.minutes}
    wall: dict[str, float] = {}

    t = time.perf_counter()
    cut(golden_dir() / "raw" / f"{vid}.mp4", work / "original.mp4", a.minutes * 60)
    norm = work / "normalized.mp4"
    if not norm.exists():
        run_normalize(work / "original.mp4", work, cfg)
    wall["S2_normalize"] = time.perf_counter() - t

    reg = ModelRegistry.load()
    det_model = rt.model(str(reg.resolve(detector_model_name(cfg))))
    t = time.perf_counter()
    gbm = GbmModel.load(reg.resolve(SHOTS_MODEL))
    clf = ShotClassifier(gbm, DFineDetector(det_model, imgsz=cfg.detector_imgsz, conf=cfg.conf_threshold))
    seg_res = run_shots_detailed(norm, cfg, classifier=clf)
    wall["S2b_shots"] = time.perf_counter() - t
    tl = seg_res.timeline
    (work / "shots.json").write_text(tl.model_dump_json())
    duration_ms = tl.shots[-1].t1_ms
    t = time.perf_counter()
    segs = plan_segments(tl, duration_ms, cfg)
    wall["plan"] = time.perf_counter() - t
    res["shots"] = len(tl.shots)
    res["main_wide_coverage"] = round(tl.coverage(), 3)
    res["segments"] = [s.model_dump() for s in segs]

    embedder, sha = load_embedder(runtime=emb_rt)
    t = time.perf_counter()
    cal = run_calibrate(norm, tl, work / "models", cfg, detector=DFineDetector(det_model, conf=cfg.conf_threshold),
                        embedder=embedder, embedder_sha256=sha)
    wall["S3_calibrate"] = time.perf_counter() - t
    res["calibrate"] = {"frames": cal.frames, "crops": cal.crops, "timings_s": cal.timings_s,
                        **{k: cal.stats[k] for k in ("cluster_sizes", "split", "cv_fisher", "outliers", "outlier_share",
                                                     "min_share", "centroid_distance", "pca_explained")}}

    seg_stats = []
    det_low = DFineDetector(det_model, imgsz=cfg.detector_imgsz, conf=LOW_CONF)
    t_inf = 0.0
    for s in segs[: a.max_segments]:
        t = time.perf_counter()
        out = run_infer(norm, s, cal.team_model, work / f"seg{s.index:03d}", cfg, detector=det_low,
                        embedder=embedder, embedder_sha256=sha, votes_per_track=a.votes)
        t_inf += time.perf_counter() - t
        st = out.stats
        secs = (s.t1_ms - s.t0_ms) / 1000
        frag = st["tracks"] / max(st["median_players_per_frame"], 1)
        seg_stats.append({"index": s.index, "seconds": secs, "frames": out.frames, "fps_measured": out.fps_measured,
                          "tracks": st["tracks"], "median_visible": st["median_players_per_frame"],
                          "ids_per_player": round(frag, 2), "ids_per_player_per_90s": round(frag * 90 / secs, 1),
                          "median_track_frames": st["median_track_frames"], "ball_frames": st["ball_frames"],
                          "team_tracks": st["team_tracks"], "vote_unanimous": st["vote_unanimous"],
                          "vote_crops": st["vote_crops"], "timings_s": st["timings_s"]})
        if s.index == 0 or s.t1_ms - s.t0_ms == max(x.t1_ms - x.t0_ms for x in segs):
            track_team_grid(norm, out.paths["detections"], work / f"track_teams_seg{s.index:03d}.jpg")
    wall["S4_infer"] = t_inf
    res["infer"] = seg_stats
    secs = sum(x["seconds"] for x in seg_stats)
    if secs:
        # time-weighted fragmentation over all analysed segments (SP-05 measured contiguous 90 s windows)
        res["ids_per_player_per_90s"] = round(sum(x["ids_per_player_per_90s"] * x["seconds"] for x in seg_stats)
                                              / secs, 1)
        res["ids_per_player_per_min"] = round(res["ids_per_player_per_90s"] / 1.5, 1)
        long = [x for x in seg_stats if x["seconds"] >= 60]
        if long:
            res["ids_per_player_per_90s_segments_ge_60s"] = round(
                sum(x["ids_per_player_per_90s"] * x["seconds"] for x in long) / sum(x["seconds"] for x in long), 1)
        res["s4_fps"] = round(sum(x["frames"] for x in seg_stats) / t_inf, 2)
    if vid in SP05_BASELINE:
        res["sp05_bytetrack_ids_per_player_per_90s"] = SP05_BASELINE[vid]
    res["wall_s"] = {k: round(v, 1) for k, v in wall.items()}
    print(json.dumps({k: v for k, v in res.items() if k not in ("segments", "infer")}, indent=1), flush=True)
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--videos", nargs="+", default=["MZjaWGwbtEc", "TyCCa3tyhZw", "BukNW8GRI5A"])
    ap.add_argument("--minutes", type=int, default=5)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--provider", default=None, help="detector runtime (ort_cpu / ort_coreml / ...)")
    ap.add_argument("--embedder-provider", default="ort_cpu")
    ap.add_argument("--max-segments", type=int, default=100)
    ap.add_argument("--votes", type=int, default=10)
    ap.add_argument("--report", type=Path)
    a = ap.parse_args()
    a.out_dir = a.out_dir.resolve()
    if ROOT in a.out_dir.parents or a.out_dir == ROOT:
        sys.exit("--out-dir must be outside the repo (videos / frames are never committed)")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    rt = Runtime.select(a.provider)
    emb_rt = Runtime.select(a.embedder_provider) if a.embedder_provider else None
    results = [run_video(v, a, rt, emb_rt) for v in a.videos]
    summary = {"format": "pv-pipeline-w3/v1", "provider": rt.provider.value,
               "embedder_provider": emb_rt.provider.value if emb_rt else None, "minutes": a.minutes,
               "votes_per_track": a.votes, "videos": results}
    if a.report:
        a.report.parent.mkdir(parents=True, exist_ok=True)
        a.report.write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
