# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Ahmet Harun Ertürk
"""Project identity / naming variables - the single place a new CV project customises.

Everything the UI, the API docs and the packaging tools need in order to stop
being project-specific lives here, driven by environment variables (usually the
local ``.env`` - it is loaded right here, so every importer sees real values).
Defaults use generic object-detection wording for a new project.

Variables (full reference in ``.env.example`` / ``USAGE.md``):

    APP_NAME          brand name for titles, header, login and API docs
    APP_TAGLINE       short subtitle next to the brand name
    UNIT_SINGULAR     word for ONE detection   (default "object")
    UNIT_PLURAL       word for MANY detections (default "objects")
    PAGE_SUBTITLE     hero paragraph   (optional - whole-sentence override)
    LOGIN_SUBTITLE    login card hint  (optional - whole-sentence override)
    UPLOAD_MAX_MB     upload size limit in megabytes (default 10)
    LABEL_ALIASES     "raw_class=Displayed Name, ..." UI-facing class names
    BLOCKED_WEIGHTS   "*.pt" file names the app refuses to serve

Sentences are COMPOSED from these words (``page_subtitle()`` etc.) and handed
to the UI through ``window.__APP_META__`` (see ``ui_meta``), so no project
name is ever hardcoded in the app, the templates or the JavaScript.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

try:  # python-dotenv is an app dependency; tolerate bare-stdlib environments
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    def load_dotenv(*_args, **_kwargs):  # type: ignore[misc]
        return False

# Loading .env HERE guarantees the variables are correct even when this module
# is imported before main.py's own load_dotenv() (e.g. detectors -> branding).
# Precedence: real process env > .env file > defaults below.
load_dotenv(Path(__file__).resolve().parent / ".env")


def _env(name: str, default: str) -> str:
    """Stripped environment value, falling back to ``default`` when unset/blank."""
    return (os.environ.get(name) or default).strip() or default


APP_NAME = _env("APP_NAME", "Object Detection")
APP_TAGLINE = _env("APP_TAGLINE", "YOLO Inference")
UNIT_SINGULAR = _env("UNIT_SINGULAR", "object")
UNIT_PLURAL = _env("UNIT_PLURAL", "objects")

# Optional whole-sentence overrides (other languages / freer wording). Blank
# means "use the composed default sentence" (see page_subtitle/login_subtitle).
PAGE_SUBTITLE_OVERRIDE = (os.environ.get("PAGE_SUBTITLE") or "").strip()
LOGIN_SUBTITLE_OVERRIDE = (os.environ.get("LOGIN_SUBTITLE") or "").strip()


def _read_upload_limit_mb() -> int:
    try:
        mb = int(float(_env("UPLOAD_MAX_MB", "10")))
    except ValueError:
        mb = 10
    return mb if mb > 0 else 10


UPLOAD_LIMIT_MB = _read_upload_limit_mb()
UPLOAD_LIMIT_BYTES = UPLOAD_LIMIT_MB * 1024 * 1024


def parse_label_aliases(raw: str) -> Dict[str, str]:
    """Parse ``LABEL_ALIASES`` = ``"raw_class=Displayed Name, other=Other Name"``.

    Entries are comma separated; the first ``=`` separates the raw model class
    (matched against ``detections[].class``) from the label shown in the UI.
    Malformed chunks are silently ignored.
    """
    aliases: Dict[str, str] = {}
    for chunk in (raw or "").split(","):
        if "=" not in chunk:
            continue
        raw_cls, _, pretty = chunk.partition("=")
        raw_cls, pretty = raw_cls.strip(), pretty.strip()
        if raw_cls and pretty:
            aliases[raw_cls] = pretty
    return aliases


LABEL_ALIASES = parse_label_aliases(_env("LABEL_ALIASES", ""))


def parse_blocked_weights(raw: str) -> frozenset:
    """Parse ``BLOCKED_WEIGHTS`` = comma separated weight FILE names (lowercased)."""
    return frozenset(
        part.strip().lower() for part in (raw or "").split(",") if part.strip()
    )


BLOCKED_WEIGHT_NAMES = parse_blocked_weights(_env("BLOCKED_WEIGHTS", ""))


# ----------------------------------------------------------------------
# Derived UI copy - single source for every user-visible sentence
# ----------------------------------------------------------------------

def unit(count: int) -> str:
    """Grammar helper: singular vs plural unit word for a detection count."""
    return UNIT_SINGULAR if abs(int(count)) == 1 else UNIT_PLURAL


def units(count: int) -> str:
    """"<n> <unit>" phrase, e.g. "0 objects" / "1 object" / "5 objects"."""
    return "%d %s" % (int(count), unit(count))


def page_subtitle() -> str:
    """Hero paragraph under the page title."""
    if PAGE_SUBTITLE_OVERRIDE:
        return PAGE_SUBTITLE_OVERRIDE
    return "Upload an image to detect %s using the trained YOLO model." % UNIT_PLURAL


def login_subtitle() -> str:
    """Hint sentence on the login card."""
    if LOGIN_SUBTITLE_OVERRIDE:
        return LOGIN_SUBTITLE_OVERRIDE
    return "Sign in to use the %s tools." % APP_NAME.lower()


def empty_title() -> str:
    """Headline of the 'nothing found' panel."""
    return "No %s detected" % UNIT_PLURAL


def detect_button() -> str:
    """Label of the (re)detect call-to-action button."""
    return "Detect %s" % UNIT_PLURAL


def result_alt() -> str:
    """Alt text of the annotated result image."""
    return "Annotated %s detection result" % UNIT_SINGULAR


def display_class(raw_name: str) -> str:
    """UI-facing label for a model class (``LABEL_ALIASES`` aware)."""
    return LABEL_ALIASES.get(str(raw_name), str(raw_name))


def decorate_detections(detections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach ``display_class`` to every detection (copy - input untouched).

    The raw ``class`` key stays authoritative for logs, db rows and machine
    consumers; UI surfaces prefer ``display_class`` (fallback: ``class``).
    """
    decorated: List[Dict[str, Any]] = []
    for det in detections or []:
        item = dict(det)
        item["display_class"] = display_class(item.get("class", ""))
        decorated.append(item)
    return decorated


def ui_meta() -> Dict[str, Any]:
    """JSON-safe branding payload for the UI (``window.__APP_META__``)."""
    return {
        "appName": APP_NAME,
        "appTagline": APP_TAGLINE,
        "unitSingular": UNIT_SINGULAR,
        "unitPlural": UNIT_PLURAL,
        "uploadLimitMb": UPLOAD_LIMIT_MB,
        "detectBtn": detect_button(),
        "emptyTitle": empty_title(),
    }
