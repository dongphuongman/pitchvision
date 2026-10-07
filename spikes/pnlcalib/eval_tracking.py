"""Camera tracking vs keyframe interpolation on real segments (run with the workspace env: uv run python ...)."""
import json, sys, tempfile, time
from pathlib import Path
import numpy as np
from cv_core.io.parquet import read_homography
from cv_core.io.video import VideoReader
from cv_core.pitch import HeatmapPitchModel, h_quality
from cv_core.registry import ModelRegistry
from cv_core.runtime import Runtime
from pitchvision_schemas import PipelineConfig, Segment
from pitchvision_worker.stages.pitch import run_pitch

HERE = Path(__file__).parent
lines = (HERE / "plan.txt").read_text().strip().splitlines()
segs = json.loads(lines[0])[:: max(1, len(json.loads(lines[0])) // 6)][:6]
cuts = [int(x) for x in lines[1].split(",")]
reg = ModelRegistry.load()
e = reg.entries["pitch_keypoints"]
model = HeatmapPitchModel(Runtime.select(None).model(str(reg.resolve("pitch_keypoints"))), world=e.extra["keypoints_world"],
                          threshold=float(e.extra["threshold"]))
cfg = PipelineConfig(keypoint_every_n=25)
video = HERE / "normalized.mp4"
res = {"tracked": [], "interp": []}
for s in segs:
    seg = Segment(**s)
    for mode, track in (("tracked", True), ("interp", False)):
        with tempfile.TemporaryDirectory() as tmp:
            t0 = time.perf_counter()
            out = run_pitch(video, seg, Path(tmp), cfg, model=model, cuts_ms=cuts, track_camera=track)
            el = time.perf_counter() - t0
            frames, Hs, _q = read_homography(out.paths["homography"])
        rd = VideoReader(video, seg.t0_ms / 1000, seg.t1_ms / 1000, stride=5, target_fps=25.0)
        hq = []
        lookup = {int(f): k for k, f in enumerate(frames)}
        for b in rd.batches(16):
            for fr in b:
                k = lookup.get(fr.idx)
                H = Hs[k] if k is not None else None
                hq.append(h_quality(fr.image, np.linalg.inv(H)) if H is not None and np.isfinite(H).all() else 0.0)
        res[mode] += hq
        print(seg.index, mode, f"mean q {np.mean(hq):.3f}  usable {np.mean(np.array(hq) >= 0.5):.2f}  {el:.0f}s")
for m, v in res.items():
    print(m, "ALL mean", round(float(np.mean(v)), 3), "usable", round(float(np.mean(np.array(v) >= 0.5)), 3), len(v))
