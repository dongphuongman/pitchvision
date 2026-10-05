"""Detection eval: AP@0.5 per class + mAP@50, overall and per stratum.

GT = COCO instances JSON (CVAT "COCO 1.0" export), categories named player / goalkeeper / referee / ball;
image file_name = ``{video_id}/{frame_idx:06d}.jpg``. Predictions = COCO results list
``[{"image_id", "category_id", "bbox": [x, y, w, h], "score"}]`` using the GT's image and category ids.

    uv run python eval/run_detection.py --gt labels/det_coco.json --pred out/dfine_results.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from pveval.formats import load_coco
from pveval.golden import GOLDEN_JSON, STRATA, load_golden
from pveval.metrics import DetectionReport, by_group, detection_ap50
from pveval.report import group_tables, table


def _row(r: DetectionReport, names: dict[int, str]) -> dict[str, float]:
    row: dict[str, float] = {"mAP50": r.map50, "n_gt": sum(r.n_gt.values())}
    row.update({names.get(c, str(c)): ap for c, ap in r.ap50.items()})
    return row


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gt", type=Path, required=True)
    ap.add_argument("--pred", type=Path, required=True)
    ap.add_argument("--golden", type=Path, default=GOLDEN_JSON)
    ap.add_argument("--iou", type=float, default=0.5)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()

    golden = load_golden(a.golden)
    coco = load_coco(a.gt, a.pred)
    cols = ["n_gt", "mAP50", *coco.class_names.values()]

    def on(videos: set[str]) -> dict[str, float]:
        keep = {i for i, v in coco.image_video.items() if v in videos}
        r = detection_ap50([p for p in coco.preds if p.image_id in keep],
                           [g for g in coco.gts if g.image_id in keep], a.iou)
        return _row(r, coco.class_names)

    videos = sorted(set(coco.image_video.values()))
    print(table("per match", {v: on({v}) for v in videos}, cols))
    strata = {v: golden.get(v, {}) for v in videos}
    tables = by_group(strata, STRATA, lambda ids: on(set(ids)))
    print("\n" + group_tables(tables, cols))
    overall = on(set(videos))
    print("\n" + table("ALL", {"all": overall}, cols))
    if a.out:
        a.out.write_text(json.dumps({"overall": overall, "by_group": {t.key: t.rows for t in tables}}, indent=1))


if __name__ == "__main__":
    main()
