# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Ahmet Harun Ertürk
"""End-to-end check on a real photo through main._run_inference (no HTTP server).

Usage:
    python e2e_real.py --image <photo.jpg>                     # default model, two thresholds
    python e2e_real.py --image <photo.jpg> --model yolo.pt --confidence 0.30

Options:
    --image PATH        path to a real test photo (JPG/PNG/WEBP)
    --model NAME        weight file name as listed by GET /models (default: MODEL_PATH)
    --confidence F ...  one or more confidence thresholds (default: 0.60 0.30)
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import main  # noqa: E402
from PIL import Image  # noqa: E402

SORTED_SCHEMA_KEYS = ["bbox", "class", "class_id", "confidence", "display_class"]

parser = argparse.ArgumentParser(description="Real-photo end-to-end inference check.")
parser.add_argument("--image", required=True, help="path to a real test photo")
parser.add_argument("--model", default=None,
                    help="weights name from GET /models (default: MODEL_PATH)")
parser.add_argument("--confidence", nargs="+", type=float, default=[0.60, 0.30],
                    help="confidence threshold(s) to exercise")
args = parser.parse_args()

photo = Path(args.image)
image = Image.open(photo)
image.load()
image = image.convert("RGB")
print("photo:", photo, image.size, flush=True)

model_name, weights = main._resolve_model_path(args.model)
print("model:", model_name, "->", weights, flush=True)

failures = 0
for conf in args.confidence:
    t0 = time.time()
    dets = main._run_inference(image, conf, model_name, weights)
    dt = time.time() - t0
    print(f"conf={conf}  n={len(dets)}  elapsed={dt:.1f}s", flush=True)
    for d in dets[:8]:
        print("   ", d, flush=True)
    schema_ok = all(sorted(d) == SORTED_SCHEMA_KEYS
                    and sorted(d["bbox"]) == ["x1", "x2", "y1", "y2"]
                    and isinstance(d["class_id"], int)
                    and isinstance(d["confidence"], float)
                    and isinstance(d.get("display_class"), str)
                    for d in dets)
    if not schema_ok:
        failures += 1
        print("    SCHEMA MISMATCH", flush=True)

print("E2E", "OK" if failures == 0 else f"FAILED ({failures})", flush=True)
sys.exit(0 if failures == 0 else 1)
