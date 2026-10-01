# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Ahmet Harun Ertürk
"""Small model-free checks for the YOLO detection adapter and app wiring."""

from pathlib import Path
from types import SimpleNamespace
import sys

import branding
from detectors import DetectorError, build_detector, extract_detections, weight_name_ok


class Value:
    def __init__(self, value):
        self.value = value

    def item(self):
        return self.value

    def tolist(self):
        return self.value


class Box:
    def __init__(self, xyxy, class_id, confidence):
        self.xyxy = [Value(xyxy)]
        self.cls = [Value(class_id)]
        self.conf = [Value(confidence)]


result = SimpleNamespace(boxes=[
    Box([1.2, 2.4, 30.6, 40.1], 1, 0.42),
    Box([5, 6, 20, 25], 0, 0.87),
])
detections = extract_detections(result, {0: "bird", 1: "cat"})
assert [item["class"] for item in detections] == ["bird", "cat"]
assert detections[1]["bbox"] == {"x1": 1, "y1": 2, "x2": 31, "y2": 40}
assert extract_detections(SimpleNamespace(boxes=[]), {}) == []
try:
    extract_detections(SimpleNamespace(boxes=None), {})
except DetectorError:
    pass
else:
    raise AssertionError("non-detection output was accepted")

assert branding.parse_label_aliases("bird=Bird,cat=Cat") == {"bird": "Bird", "cat": "Cat"}
original = branding.LABEL_ALIASES
branding.LABEL_ALIASES = {"bird": "Bird"}
try:
    assert branding.decorate_detections(detections)[0]["display_class"] == "Bird"
finally:
    branding.LABEL_ALIASES = original

original_blocks = branding.BLOCKED_WEIGHT_NAMES
branding.BLOCKED_WEIGHT_NAMES = frozenset({"blocked.pt"})
try:
    assert weight_name_ok("sample.pt")
    assert not weight_name_ok("BLOCKED.PT")
finally:
    branding.BLOCKED_WEIGHT_NAMES = original_blocks

original_ultralytics = sys.modules.get("ultralytics")
sys.modules["ultralytics"] = SimpleNamespace(
    YOLO=lambda path: SimpleNamespace(task="classify")
)
try:
    try:
        build_detector(Path("sample.pt"))
    except DetectorError as exc:
        assert "requires object-detection" in str(exc)
    else:
        raise AssertionError("classification weights were accepted")
finally:
    if original_ultralytics is None:
        sys.modules.pop("ultralytics", None)
    else:
        sys.modules["ultralytics"] = original_ultralytics

import main  # noqa: E402
import api.server as api  # noqa: E402

assert any(route.path == "/predict" for route in main.app.routes)
assert any(route.path == "/predict" for route in api.app.routes)
assert main.MODEL_PATH.suffix.lower() == ".pt"
assert isinstance(Path(main.MODEL_PATH), Path)
print("SMOKE OK")
