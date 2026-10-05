"""Object detection (AD §6.5): D-FINE (Apache-2.0) on the cv_core Runtime."""
from cv_core.detect.dfine import COCO_PERSON, COCO_SPORTS_BALL, Detections, DFineDetector, postprocess, preprocess

__all__ = ["COCO_PERSON", "COCO_SPORTS_BALL", "DFineDetector", "Detections", "postprocess", "preprocess"]
