# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Ahmet Harun Ertürk
"""YOLO object-detection web app with env-selected weights and image overlays."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import io
import logging
import os
import threading
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from detectors import build_detector, extract_detections, weight_name_ok

# Load .env (if present) before any configuration is read - crucially BEFORE
# ``import db`` below, because db.py resolves its settings at import time.
load_dotenv(Path(__file__).resolve().parent / ".env")

import db  # noqa: E402  (intentionally imported after load_dotenv)
import branding  # noqa: E402  (UI copy / naming variables, after load_dotenv)

# --------------------------------------------------------------------------
# Configuration (environment variables always have sensible defaults)
# --------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent

MODEL_PATH = Path(os.environ.get("MODEL_PATH") or (BASE_DIR / "models" / "best.pt"))
# Folder scanned for selectable weights (*.pt). Defaults to the folder that
# holds MODEL_PATH; users pick among these SERVER-SIDE files in the web UI.
MODELS_DIR = Path(os.environ.get("MODELS_DIR") or MODEL_PATH.parent)
HOST = os.environ.get("HOST") or "0.0.0.0"
PORT = int(os.environ.get("PORT") or 8000)

MAX_UPLOAD_BYTES = branding.UPLOAD_LIMIT_BYTES  # UPLOAD_MAX_MB (see .env.example)
MAX_IMAGE_PIXELS = int(os.environ.get("MAX_IMAGE_PIXELS") or 25_000_000)
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}


def _read_confidence() -> float:
    """Confidence threshold from the CONFIDENCE env var (e.g. .env); default 0.60."""
    try:
        value = float((os.environ.get("CONFIDENCE") or "").strip())
    except ValueError:
        value = 0.60
    return value if 0.0 < value < 1.0 else 0.60


DEFAULT_CONFIDENCE = _read_confidence()

# UI copy / branding - derived from the branding.* variables (.env: APP_NAME,
# APP_TAGLINE, UNIT_*, PAGE_SUBTITLE, LOGIN_SUBTITLE, UPLOAD_MAX_MB, ...).
# Templates render these as Jinja variables and app.js receives them as JSON
# via window.__APP_META__, so no project name is hardcoded anywhere in the app.
UI_CONTEXT: Dict[str, Any] = {
    "app_name": branding.APP_NAME,
    "app_tagline": branding.APP_TAGLINE,
    "page_subtitle": branding.page_subtitle(),
    "login_subtitle": branding.login_subtitle(),
    "count_zero": branding.units(0),
    "result_alt": branding.result_alt(),
    "empty_title": branding.empty_title(),
    "detect_button": branding.detect_button(),
    "upload_limit_mb": branding.UPLOAD_LIMIT_MB,
    "default_confidence": DEFAULT_CONFIDENCE,
    "app_meta": branding.ui_meta(),
}

# Operator credentials are optional when the demo account is explicitly enabled.
# Never expose the demo account on a production deployment.
AUTH_USER = (os.environ.get("AUTH_USER") or "").strip()
AUTH_PASS = os.environ.get("AUTH_PASS") or ""
DEMO_LOGIN_ENABLED = (os.environ.get("DEMO_LOGIN_ENABLED") or "false").strip().lower() in {
    "1", "true", "yes", "on",
}
SESSION_COOKIE_NAME = "cvd_session"
SESSION_TTL_SECONDS = 24 * 60 * 60  # sign-in is valid for 24 hours

_BOX_PALETTE = (
    (37, 99, 235),    # blue
    (5, 150, 105),    # green
    (217, 119, 6),    # amber
    (124, 58, 237),   # violet
    (13, 148, 136),   # teal
    (225, 29, 72),    # rose
)
_FONT_CANDIDATES = ("DejaVuSans.ttf", "arial.ttf", "Arial.ttf", "segoeui.ttf", "Helvetica.ttc")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cv-deploy")

# Private module state: the model is loaded once and shared across requests.
_model = None
_model_error = None
_model_lock = threading.RLock()  # reentrant: loaders run inside request paths
_loaded_signature = None  # (mtime_ns, size) last attempted, successful or not


# --------------------------------------------------------------------------
# Startup
# --------------------------------------------------------------------------


def _weights_signature() -> Optional[Tuple[int, int]]:
    """(mtime_ns, size) of the weights file, or None when it is missing."""
    return _file_signature(MODEL_PATH)


def _load_model() -> None:
    """Load YOLO detection weights; record errors instead of crashing."""
    global _model, _model_error, _loaded_signature
    signature = _weights_signature()
    if signature is None:
        _model = None
        _loaded_signature = None
        _model_error = f"Model file not found: {MODEL_PATH}"
        logger.error(_model_error)
        return
    try:
        _model = build_detector(MODEL_PATH)
        _model_error = None
        _loaded_signature = signature
        logger.info("Detection model loaded from %s (%d classes)", MODEL_PATH, len(_model.names))
    except Exception as exc:  # noqa: BLE001 - surfaced through /health
        _model = None
        _loaded_signature = signature
        _model_error = f"Failed to load model: {exc}"
        logger.exception("Detection model loading failed")


def _ensure_model_current() -> None:
    """Hot-swap the default weights when their file changes."""
    if _loaded_signature == _weights_signature():
        return
    with _model_lock:
        if _loaded_signature == _weights_signature():
            return
        logger.info("Weights file changed - reloading %s", MODEL_PATH)
        _load_model()


# --------------------------------------------------------------------------
# Selectable model registry (multi-weights support)
# --------------------------------------------------------------------------
#
# Besides the default MODEL_PATH weights the user may choose any *.pt file
# that sits in MODELS_DIR. The catalog is rescanned on every request, so
# dropped-in or removed weights show up immediately. User input never becomes
# a filesystem path - it is only looked up as a key in the catalog, which
# rules out path traversal. Weights can not be uploaded (deserialising
# untrusted checkpoints would allow remote code execution); only the
# operator-provided files on disk are selectable.

_MODEL_CACHE_MAX = 2  # extra (non-default) models kept loaded in memory
_alternate_models: "OrderedDict[str, Tuple[Tuple[int, int], Any]]" = OrderedDict()


def _file_signature(path: Path) -> Optional[Tuple[int, int]]:
    """(mtime_ns, size) of a weights file, or None when it is missing."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _model_catalog() -> Dict[str, Path]:
    """Default MODEL_PATH weights plus every *.pt file found in MODELS_DIR.

    Weights the product refuses to serve (BLOCKED_WEIGHTS, see .env.example)
    are filtered out, so they vanish from the UI picker and every ``model=``
    request for them is rejected as unknown.
    """
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


def _resolve_model_path(name: Optional[str]) -> Tuple[str, Path]:
    """Map a requested model name onto a validated weights path (or the default)."""
    catalog = _model_catalog()
    requested = (name or "").strip() or MODEL_PATH.name
    if requested not in catalog:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unknown model '%s'. Pick one of the weight files listed by GET /models."
                % requested[:64]
            ),
        )
    return requested, catalog[requested]


def _get_model(model_name: str, model_path: Path) -> Any:
    """Detector for the given weights file, hot-reloaded whenever it changes."""
    if model_name == MODEL_PATH.name:
        _ensure_model_current()
        if _model is None:
            raise HTTPException(status_code=503, detail=_model_error or "Model is not loaded.")
        return _model
    signature = _file_signature(model_path)
    if signature is None:
        raise HTTPException(status_code=503, detail=f"Model file not found: {model_name}")
    with _model_lock:
        entry = _alternate_models.get(model_name)
        if entry is not None and entry[0] == signature:
            _alternate_models.move_to_end(model_name)
            return entry[1]
        logger.info("Loading weights for selected model %s", model_path)
        try:
            model = build_detector(model_path)
        except Exception as exc:  # noqa: BLE001 - report unusable weights clearly
            logger.exception("Failed to load weights for selected model %s", model_path)
            raise HTTPException(
                status_code=503,
                detail=(
                    "Weights for '%s' could not be loaded (%s). "
                    "Replace the file with a valid Ultralytics YOLO detection checkpoint."
                    % (model_name, exc)
                ),
            ) from exc
        _alternate_models[model_name] = (signature, model)
        _alternate_models.move_to_end(model_name)
        while len(_alternate_models) > _MODEL_CACHE_MAX:
            _alternate_models.popitem(last=False)
        return model


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    _load_model()
    db.setup()
    if DEMO_LOGIN_ENABLED:
        logger.warning("Demo login admin/pass is enabled; disable it before public deployment.")
    elif not _configured_login_available():
        logger.warning(
            "No non-demo AUTH_USER/AUTH_PASS configured and demo login disabled - login is unavailable."
        )
    yield


app = FastAPI(
    title=branding.APP_NAME,
    description=(
        "YOLO %s detection demo: upload an image, receive annotated detections."
        % branding.UNIT_SINGULAR
    ),
    version="1.0.0",
    lifespan=_lifespan,
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# --------------------------------------------------------------------------
# Authentication (simple hardcoded login gate)
# --------------------------------------------------------------------------
#
# NOTE on ordering: the middleware below is registered BEFORE the
# request-logging middleware on purpose - in Starlette that makes it run
# INSIDE it, so even rejected/redirected visitors are logged.


def _session_secret() -> bytes:
    """Signing key derived from the configured credentials.

    Changing credentials or the demo-login switch invalidates old cookies.
    """
    material = "|".join([
        "cv-deploy.session.v2", AUTH_USER, AUTH_PASS, str(DEMO_LOGIN_ENABLED),
    ])
    return hashlib.sha256(material.encode("utf-8")).digest()


def _session_signature(expires_at: str) -> str:
    return hmac.new(_session_secret(), expires_at.encode("ascii"), hashlib.sha256).hexdigest()[:32]


def _issue_session_token(now: Optional[float] = None) -> str:
    """Mint a signed "<unix-expiry>.<hmac>" session token."""
    expires_at = int((now if now is not None else time.time()) + SESSION_TTL_SECONDS)
    payload = str(expires_at)
    return payload + "." + _session_signature(payload)


def _session_token_valid(token: Optional[str]) -> bool:
    """True when the token carries a valid signature and has not expired."""
    if not _login_available():
        return False
    if not token or "." not in token:
        return False
    payload, _, signature = token.partition(".")
    if not payload.isdigit() or len(signature) != 32:
        return False
    if not hmac.compare_digest(signature, _session_signature(payload)):
        return False
    return int(payload) > time.time()


def _configured_login_available() -> bool:
    """Treat admin/pass as the built-in demo account, even if present in .env."""
    return bool(AUTH_USER and AUTH_PASS) and not (
        AUTH_USER == "admin" and AUTH_PASS == "pass"
    )


def _login_available() -> bool:
    return _configured_login_available() or DEMO_LOGIN_ENABLED


def _credentials_ok(username: str, password: str) -> bool:
    """Accept the configured account and, only when enabled, admin/pass."""
    configured_ok = _configured_login_available() and (
        hmac.compare_digest(username.encode("utf-8"), AUTH_USER.encode("utf-8"))
        and hmac.compare_digest(password.encode("utf-8"), AUTH_PASS.encode("utf-8"))
    )
    demo_ok = DEMO_LOGIN_ENABLED and (
        hmac.compare_digest(username.encode("utf-8"), b"admin")
        and hmac.compare_digest(password.encode("utf-8"), b"pass")
    )
    return configured_ok or demo_ok


_PUBLIC_PATHS = {"/login", "/logout", "/health"}


@app.middleware("http")
async def _require_login(request: Request, call_next):
    """Deny every request until the visitor signs in (cookie-based gate)."""
    path = request.url.path
    if path in _PUBLIC_PATHS or path.startswith("/static"):
        return await call_next(request)

    if _session_token_valid(request.cookies.get(SESSION_COOKIE_NAME)):
        return await call_next(request)

    # Navigations (GET/HEAD) go to the login form; API style calls get 401 JSON.
    if request.method in ("GET", "HEAD"):
        return RedirectResponse("/login", status_code=303)
    return JSONResponse({"detail": "Not authenticated"}, status_code=401)


# --------------------------------------------------------------------------
# Request logging
# --------------------------------------------------------------------------


def _client_ip(request: Request) -> Optional[str]:
    """Client IP, honouring X-Forwarded-For from reverse proxies (e.g. Dokploy)."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded.strip():
        return forwarded.split(",")[0].strip() or None
    return request.client.host if request.client else None


@app.middleware("http")
async def _log_http_requests(request: Request, call_next):
    """Record every request (client, device, outcome) through db.log_request."""

    if request.url.path.startswith("/static") or request.url.path == "/health":
        return await call_next(request)

    started = time.perf_counter()
    status_code = 500
    error_message = None
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    except Exception as exc:  # noqa: BLE001 - logged here, then re-raised
        error_message = str(getattr(exc, "detail", None) or exc)
        raise
    finally:
        user_agent = request.headers.get("user-agent", "")
        entry: Dict[str, Any] = {
            "ip_address": _client_ip(request),
            "user_agent": user_agent[:2000],
            **db.parse_client(user_agent),
            "method": request.method,
            "path": request.url.path,
            "status_code": status_code,
            "success": status_code < 400,
            "processing_ms": round((time.perf_counter() - started) * 1000.0, 1),
            "error_message": error_message,
        }
        extra = getattr(request.state, "log_result", None)
        if isinstance(extra, dict):
            entry.update(extra)
        db.log_request(entry)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _load_font(size: int) -> ImageFont.ImageFont:
    """Return a scalable font, falling back gracefully on slim systems."""
    for candidate in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def _decode_image(raw: bytes) -> Image.Image:
    """Decode and validate an uploaded image entirely in memory."""
    try:
        image = Image.open(io.BytesIO(raw))
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise HTTPException(status_code=413, detail="Image dimensions are too large.")
        image.load()
    except HTTPException:
        raise
    except UnidentifiedImageError as exc:
        raise HTTPException(status_code=400, detail="The uploaded file is not a valid image.") from exc
    except Exception as exc:  # noqa: BLE001 - any decoder/plugin failure means bad input
        logger.warning("Image decoding failed: %s", exc)
        raise HTTPException(status_code=400, detail="The uploaded image could not be decoded.") from exc

    if (image.format or "").upper() not in ALLOWED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail="Unsupported image format. Allowed formats: JPG, JPEG, PNG, WEBP.",
        )
    return ImageOps.exif_transpose(image)


def _extract_detections(result: Any, names: Any) -> List[Dict[str, Any]]:
    """Convert YOLO boxes into the shared JSON detection schema."""
    return extract_detections(result, names)


def _run_inference(
    image: Image.Image, confidence: float, model_name: str, model_path: Path
) -> List[Dict[str, Any]]:
    model = _get_model(model_name, model_path)
    with _model_lock:
        results = model.predict(source=image, conf=confidence, verbose=False)
    result = results[0] if isinstance(results, (list, tuple)) else results
    detections = _extract_detections(result, model.names)
    # Add display_class (LABEL_ALIASES-aware) next to the raw `class` name;
    # the raw name stays authoritative for logs / machine consumers.
    return branding.decorate_detections(detections)


def _annotate(image: Image.Image, detections: List[Dict[str, Any]]) -> Image.Image:
    """Draw labelled bounding boxes (class name + confidence) on a copy of the image."""
    canvas = image.copy()
    draw = ImageDraw.Draw(canvas)

    stroke = max(2, round(min(canvas.size) / 320))
    font_size = max(11, min(22, canvas.width // 55))
    font = _load_font(font_size)
    pad_x, pad_y = 7, 3

    for detection in detections:
        color = _BOX_PALETTE[detection["class_id"] % len(_BOX_PALETTE)]
        bbox = detection["bbox"]
        x1, y1 = max(bbox["x1"], 0), max(bbox["y1"], 0)
        x2, y2 = min(bbox["x2"], canvas.width), min(bbox["y2"], canvas.height)

        draw.rectangle([x1, y1, x2, y2], outline=color, width=stroke)

        text = "%s %.1f%%" % (detection.get("display_class") or detection["class"],
                              detection["confidence"] * 100.0)
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        text_w, text_h = right - left, bottom - top

        label_x = max(0, min(x1, canvas.width - (text_w + 2 * pad_x)))
        label_y = y1 - text_h - 2 * pad_y - stroke
        if label_y < 0:
            label_y = min(y1 + stroke, canvas.height - (text_h + 2 * pad_y))

        draw.rectangle(
            [label_x, label_y, label_x + text_w + 2 * pad_x, label_y + text_h + 2 * pad_y],
            fill=color,
        )
        draw.text(
            (label_x + pad_x - left, label_y + pad_y - top),
            text,
            fill=(255, 255, 255),
            font=font,
        )

    return canvas


def _encode_image(image: Image.Image) -> Tuple[str, str]:
    """Encode the annotated image to base64 without touching the filesystem."""
    buffer = io.BytesIO()
    if image.mode == "RGBA":
        image.save(buffer, format="PNG", optimize=True)
        return base64.b64encode(buffer.getvalue()).decode("ascii"), "png"
    image.convert("RGB").save(buffer, format="JPEG", quality=92)
    return base64.b64encode(buffer.getvalue()).decode("ascii"), "jpeg"


def _process_image(raw: bytes, confidence: float, model_name: str, model_path: Path) -> Dict[str, Any]:
    """Full inference pipeline: decode -> predict -> annotate -> encode."""
    source = _decode_image(raw)
    draw_base = source.convert("RGBA") if "A" in source.getbands() else source.convert("RGB")

    started = time.perf_counter()
    detections = _run_inference(draw_base.convert("RGB"), confidence, model_name, model_path)
    annotated = _annotate(draw_base, detections)
    encoded, image_format = _encode_image(annotated)
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    return {
        "success": True,
        "count": len(detections),
        "model": model_name,
        "detections": detections,
        "image": encoded,
        "image_format": image_format,
        "image_width": draw_base.width,
        "image_height": draw_base.height,
        "processing_time_ms": round(elapsed_ms, 1),
    }


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request) -> Response:
    """Show the (single-account) login form."""
    if _session_token_valid(request.cookies.get(SESSION_COOKIE_NAME)):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", UI_CONTEXT)


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request) -> Response:
    """Validate the submitted credentials and issue a signed session cookie."""
    form = await request.form()
    if not _login_available():
        raise HTTPException(status_code=503, detail="Login is not configured.")
    username = str(form.get("username") or "").strip()
    password = str(form.get("password") or "")
    if not _credentials_ok(username, password):
        logger.info("failed sign-in attempt for user %r", username[:64])
        return templates.TemplateResponse(
            request, "login.html", {**UI_CONTEXT, "error": "Invalid username or password."}
        )

    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        _issue_session_token(),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=(request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"),
        samesite="lax",
        path="/",
    )
    return response


@app.get("/logout")
async def logout() -> RedirectResponse:
    """Clear the session cookie and return to the login screen."""
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    """Serve the main web interface."""
    return templates.TemplateResponse(request, "index.html", UI_CONTEXT)


@app.get("/health")
def health() -> JSONResponse:
    """Container health-check / model readiness endpoint."""
    _ensure_model_current()
    payload: Dict[str, Any] = {
        "status": "ok" if _model is not None else "degraded",
        "model_loaded": _model is not None,
    }
    if _model is not None:
        payload["classes"] = len(_model.names)
    if _model_error is not None:
        payload["error"] = _model_error
    return JSONResponse(payload, status_code=200 if _model is not None else 503)


@app.get("/models")
def model_list() -> Dict[str, Any]:
    """Selectable weight files (*.pt) - the web UI fills its model picker from here."""
    catalog = _model_catalog()
    items: List[Dict[str, Any]] = []
    for name, path in catalog.items():
        try:
            size: Optional[int] = path.stat().st_size
        except OSError:
            size = None
        items.append({"name": name, "size_bytes": size})
    return {"default": MODEL_PATH.name, "models": items}


@app.get("/predict")
def predict_info() -> Dict[str, str]:
    """Describe the detection endpoint for GET probes."""
    return {
        "message": "Send POST multipart/form-data with field 'image' (optional 'confidence' overrides the CONFIDENCE default; optional 'model' picks a weight file listed by GET /models).",
    }


@app.post("/predict")
async def predict(
    request: Request,
    image: UploadFile = File(...),
    confidence: float = Form(DEFAULT_CONFIDENCE),
    model: Optional[str] = Form(None),
) -> Dict[str, Any]:
    """Run detection on an uploaded image and return annotated results."""
    filename = (image.filename or "").strip() or "upload"
    try:
        if Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail="Unsupported image format. Allowed formats: JPG, JPEG, PNG, WEBP.",
            )

        raw = await image.read(MAX_UPLOAD_BYTES + 1)
        if not raw:
            raise HTTPException(status_code=400, detail="The uploaded file is empty.")
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Image is larger than the %d MB limit." % branding.UPLOAD_LIMIT_MB,
            )
        if not 0.0 < confidence < 1.0:
            raise HTTPException(status_code=400, detail="Confidence must be a value between 0 and 1.")

        model_name, model_path = _resolve_model_path(model)
        result = await asyncio.to_thread(_process_image, raw, confidence, model_name, model_path)
    except HTTPException as exc:
        request.state.log_result = {"image_name": filename, "error_message": str(exc.detail)}
        raise
    except Exception:
        logger.exception("Prediction failed for file '%s'", filename)
        request.state.log_result = {"image_name": filename, "error_message": "internal error"}
        raise HTTPException(status_code=500, detail="Prediction failed. Please try again.")

    request.state.log_result = {
        "image_name": filename,
        "detections": result["count"],
        "classes": sorted({str(det["class"]) for det in result["detections"]}),
        "top_confidence": max((float(det["confidence"]) for det in result["detections"]), default=None),
        "processing_ms": result["processing_time_ms"],
    }
    return result


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=HOST, port=PORT)
