"""S4 throughput on one segment of a normalized golden clip, per detector provider (M1-CV-10).

    uv run python eval/bench_infer.py --normalized /tmp/pv_w3/MZjaWGwbtEc/normalized.mp4 \
        --team-model /tmp/pv_w3/MZjaWGwbtEc/models/team_model.json --seconds 120 --providers ort_cpu ort_coreml \
        --out-dir /tmp/pv_bench --report eval/results/infer_m1cv10_throughput.json

The embedder (SigLIP) runs on ``--embedder-provider`` (default CPU EP) in every run; the report splits wall time into
detect / track+mask+crops / team vote so the detector providers can be compared on their own.
"""
from __future__ import annotations

import argparse
import json
import platform
from pathlib import Path

from cv_core.detect import DFineDetector
from cv_core.registry import ModelRegistry
from cv_core.runtime import Runtime
from pitchvision_schemas import PipelineConfig, Segment
from pitchvision_worker.stages.infer import LOW_CONF, run_infer
from pitchvision_worker.stages.models import detector_model_name, load_embedder


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--normalized", type=Path, required=True)
    ap.add_argument("--team-model", type=Path, required=True)
    ap.add_argument("--t0", type=float, default=0.0)
    ap.add_argument("--seconds", type=float, default=120.0)
    ap.add_argument("--providers", nargs="+", default=["ort_cpu", "ort_coreml"])
    ap.add_argument("--embedder-provider", default="ort_cpu")
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--report", type=Path)
    a = ap.parse_args()
    cfg = PipelineConfig(frame_stride=a.stride)
    seg = Segment(index=0, t0_ms=round(a.t0 * 1000), t1_ms=round((a.t0 + a.seconds) * 1000))
    reg = ModelRegistry.load()
    embedder, sha = load_embedder(runtime=Runtime.select(a.embedder_provider))
    runs = {}
    for prov in a.providers:
        rt = Runtime.select(prov)
        model = rt.model(str(reg.resolve(detector_model_name(cfg))), warmup=True)
        det = DFineDetector(model, imgsz=cfg.detector_imgsz, conf=LOW_CONF)
        out = run_infer(a.normalized, seg, a.team_model, a.out_dir / prov, cfg, detector=det, embedder=embedder,
                        embedder_sha256=sha)
        t = out.stats["timings_s"]
        runs[prov] = {"active_provider": model.provider.value, "frames": out.frames, "fps_end_to_end": out.fps_measured,
                      "fps_detect_only": round(out.frames / t["detect"], 1),
                      "fps_without_team_vote": round(out.frames / (t["total"] - t["team_vote"]), 1),
                      "vote_crops": out.stats["vote_crops"], "ms_per_vote_crop": round(
                          1000 * t["team_vote"] / max(out.stats["vote_crops"], 1), 1), "timings_s": t}
        print(prov, json.dumps(runs[prov]), flush=True)
    rep = {"format": "pv-infer-bench/v1", "machine": f"{platform.machine()} {platform.platform()}",
           "video": a.normalized.parent.name, "segment": seg.model_dump(), "stride": a.stride,
           "embedder_provider": a.embedder_provider, "runs": runs}
    if a.report:
        a.report.write_text(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
