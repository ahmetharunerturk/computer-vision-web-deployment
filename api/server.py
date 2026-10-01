# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Ahmet Harun Ertürk
"""Standalone Ultralytics YOLO detection API for container deployments.

This service returns box data; use ``main:app`` for the annotated web UI.

Endpoints
---------
GET  /health    liveness + loaded backend info
GET  /models    selectable weights catalog (same rules as the web app)
POST /predict   multipart image -> {model, detections[{class,class_id,confidence,bbox}]}
"""

from __future__ import annotations

import io
import logging
import os
import sys
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from PIL import Image, ImageOps, UnidentifiedImageError

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:          # allow `python api/server.py` from anywhere
    sys.path.insert(0, str(BASE_DIR))
load_dotenv(BASE_DIR / ".env")

from detectors import build_detector, extract_detections, weight_name_ok  # noqa: E402
import branding  # noqa: E402  (imported after load_dotenv)

logger = logging.getLogger("api.server")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

MODEL_PATH = Path(os.environ.get("MODEL_PATH") or (BASE_DIR / "models" / "best.pt"))
MODELS_DIR = Path(os.environ.get("MODELS_DIR") or MODEL_PATH.parent)
MAX_UPLOAD_BYTES = branding.UPLOAD_LIMIT_BYTES
MAX_IMAGE_PIXELS = int(os.environ.get("MAX_IMAGE_PIXELS") or 25_000_000)
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}

app = FastAPI(title="%s API" % branding.APP_NAME, version="1.0")

_model_lock = threading.Lock()
_models: "OrderedDict[str, Tuple[Tuple[int, int], Any]]" = OrderedDict()
_MODEL_CACHE_MAX = 2


def _file_signature(path: Path) -> Optional[Tuple[int, int]]:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _model_catalog() -> Dict[str, Path]:
    """Selectable *.pt weights; retired names (detectors.BLOCKED_WEIGHT_NAMES) hidden."""
    catalog: Dict[str, Path] = {}
    if MODEL_PATH.suffix.lower() == ".pt" and weight_name_ok(MODEL_PATH.name):
        catalog[MODEL_PATH.name] = MODEL_PATH
    try:
        for path in sorted(MODELS_DIR.iterdir()):
            if (path.is_file() and path.suffix.lower() == ".pt"
                    and weight_name_ok(path.name)):
                catalog.setdefault(path.name, path)
    except OSError:
        pass
    return {name: catalog[name] for name in sorted(catalog)}


def _resolve(name: Optional[str]) -> Tuple[str, Path]:
    catalog = _model_catalog()
    requested = (name or "").strip() or MODEL_PATH.name
    if requested not in catalog:
        raise HTTPException(status_code=400,
                            detail="Unknown model '%s'. See GET /models." % requested[:64])
    return requested, catalog[requested]


def _get_model(name: str, path: Path) -> Any:
    signature = _file_signature(path)
    if signature is None:
        raise HTTPException(status_code=503, detail=f"Model file not found: {name}")
    with _model_lock:
        entry = _models.get(name)
        if entry is not None and entry[0] == signature:
            _models.move_to_end(name)
            return entry[1]
        model = build_detector(path)
        _models[name] = (signature, model)
        _models.move_to_end(name)
        while len(_models) > _MODEL_CACHE_MAX:
            _models.popitem(last=False)
        return model


@app.get("/health")
def health() -> JSONResponse:
    catalog = _model_catalog()
    error = None
    default = catalog.get(MODEL_PATH.name)
    if default is None:
        error = f"Model file not found: {MODEL_PATH}"
    else:
        try:
            _get_model(MODEL_PATH.name, default)
        except Exception as exc:
            error = str(exc)
    payload = {"status": "ok" if error is None else "degraded", "model": MODEL_PATH.name,
               "models": sorted(catalog)}
    if error is not None:
        payload["error"] = error
    return JSONResponse(payload, status_code=200 if error is None else 503)


@app.get("/models")
def models() -> Dict[str, Any]:
    catalog = _model_catalog()
    return {"default": MODEL_PATH.name, "models": sorted(catalog)}


@app.post("/predict")
def predict(image: UploadFile = File(...),
            confidence: float = Form(0.6),
            model: Optional[str] = Form(None)) -> Dict[str, Any]:
    if not 0.0 < confidence < 1.0:
        raise HTTPException(status_code=400, detail="Confidence must be between 0 and 1.")
    raw = image.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413,
                            detail="Image exceeds the %d MB limit." % branding.UPLOAD_LIMIT_MB)
    try:
        pil = Image.open(io.BytesIO(raw))
        if pil.width * pil.height > MAX_IMAGE_PIXELS:
            raise HTTPException(status_code=413, detail="Image dimensions are too large.")
        pil.load()
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=400, detail="The image could not be decoded.") from exc
    if (pil.format or "").upper() not in ALLOWED_FORMATS:
        raise HTTPException(status_code=400, detail="Allowed formats: JPG, JPEG, PNG, WEBP.")
    pil = ImageOps.exif_transpose(pil).convert("RGB")

    name, path = _resolve(model)
    detector = _get_model(name, path)
    with _model_lock:
        results = detector.predict(source=pil, conf=confidence, verbose=False)
    result = results[0] if isinstance(results, (list, tuple)) else results
    detections: List[Dict[str, Any]] = branding.decorate_detections(
        extract_detections(result, getattr(detector, "names", {}))
    )
    return {"model": name, "confidence": confidence, "detections": detections}


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host=os.environ.get("HOST", "0.0.0.0"),
                port=int(os.environ.get("PORT") or 8000))
