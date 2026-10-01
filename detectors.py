# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Ahmet Harun Ertürk
"""Load Ultralytics YOLO detection weights and normalize bounding boxes."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import branding


class DetectorError(RuntimeError):
    """The supplied weights cannot be used for object detection."""


def weight_name_ok(name: str) -> bool:
    """Apply the optional operator-maintained filename block list."""
    return Path(str(name)).name.lower() not in branding.BLOCKED_WEIGHT_NAMES


def build_detector(model_path: Path) -> Any:
    """Load one operator-provided Ultralytics YOLO detection checkpoint."""
    if model_path.suffix.lower() != ".pt" or not weight_name_ok(model_path.name):
        raise DetectorError(f"Weights '{model_path.name}' are not allowed.")

    from ultralytics import YOLO

    try:
        model = YOLO(str(model_path))
    except Exception as exc:
        raise DetectorError(
            f"Could not load '{model_path.name}' as an Ultralytics YOLO checkpoint: {exc}"
        ) from exc
    if getattr(model, "task", None) != "detect":
        raise DetectorError(
            f"'{model_path.name}' is a YOLO {getattr(model, 'task', 'unknown')} model; "
            "this app requires object-detection weights with bounding boxes."
        )
    return model


def extract_detections(result: Any, names: Any) -> List[Dict[str, Any]]:
    """Return detected classes, scores and pixel-coordinate boxes."""
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        raise DetectorError("The model returned no detection boxes.")
    label_map = names if isinstance(names, dict) else dict(enumerate(names or []))
    detections: List[Dict[str, Any]] = []
    for box in boxes:
        x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
        class_id = int(box.cls[0].item())
        detections.append({
            "class": str(label_map.get(class_id, f"class_{class_id}")),
            "class_id": class_id,
            "confidence": round(float(box.conf[0].item()), 3),
            "bbox": {
                "x1": int(round(x1)), "y1": int(round(y1)),
                "x2": int(round(x2)), "y2": int(round(y2)),
            },
        })
    detections.sort(key=lambda item: item["confidence"], reverse=True)
    return detections
