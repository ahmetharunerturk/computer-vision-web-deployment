# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Ahmet Harun Ertürk
"""Request logging for the CV detection web app, backed by PostgreSQL.

Application requests are recorded together with client information
(IP address, user agent, parsed device/browser/OS) and - for POST /predict -
the detection outcome. Rows live in a dedicated PostgreSQL schema
(default: ``cv_detection``) that is created automatically at startup.

Inserts run on a background thread fed by a small in-memory queue, so the
request path is never blocked by the database. When PostgreSQL is
unreachable the application keeps serving traffic and pauses request logging.
"""

from __future__ import annotations

import logging
import os
import queue
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import psycopg
from dotenv import load_dotenv

logger = logging.getLogger("cv-deploy.db")

# Make sure .env values are visible before the settings below are resolved,
# no matter which module imports this one first (cf. main.py).
load_dotenv(Path(__file__).resolve().parent / ".env")

# --------------------------------------------------------------------------
# Configuration (read from the environment / .env)
# --------------------------------------------------------------------------

LOG_ENABLED = (os.environ.get("LOG_ENABLED") or "false").strip().lower() in {
    "1", "true", "yes", "on",
}
DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()

_raw_schema = (os.environ.get("LOG_SCHEMA") or "cv_detection").strip()
SCHEMA = _raw_schema if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", _raw_schema) else "cv_detection"
TABLE = f"{SCHEMA}.request_log"

INSERT_COLUMNS = (
    "ip_address",
    "user_agent",
    "device_type",
    "browser",
    "operating_system",
    "method",
    "path",
    "status_code",
    "success",
    "detections",
    "classes",
    "top_confidence",
    "processing_ms",
    "image_name",
    "error_message",
)

_BLANK_ENTRY: Dict[str, Any] = {column: None for column in INSERT_COLUMNS}

_INSERT_SQL = (
    f"INSERT INTO {TABLE} ({', '.join(INSERT_COLUMNS)}) "
    f"VALUES ({', '.join(['%s'] * len(INSERT_COLUMNS))})"
)

_DDL = f"""
CREATE SCHEMA IF NOT EXISTS {SCHEMA};
CREATE TABLE IF NOT EXISTS {TABLE} (
    id               BIGSERIAL PRIMARY KEY,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    ip_address       TEXT,
    user_agent       TEXT,
    device_type      TEXT,
    browser          TEXT,
    operating_system TEXT,
    method           TEXT NOT NULL,
    path             TEXT NOT NULL,
    status_code      INTEGER,
    success          BOOLEAN,
    detections       INTEGER,
    classes          TEXT[],
    top_confidence   DOUBLE PRECISION,
    processing_ms    DOUBLE PRECISION,
    image_name       TEXT,
    error_message    TEXT
);
CREATE INDEX IF NOT EXISTS ix_request_log_created_at ON {TABLE} (created_at DESC);
CREATE INDEX IF NOT EXISTS ix_request_log_ip ON {TABLE} (ip_address);
"""

_QUEUE: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue(maxsize=2000)
_WORKER: Optional[threading.Thread] = None
_START_LOCK = threading.Lock()


def enabled() -> bool:
    """True when request logging is switched on and configured."""
    return LOG_ENABLED and bool(DATABASE_URL)


def parse_client(user_agent: str) -> Dict[str, str]:
    """Best-effort device / browser / OS classification from a User-Agent."""

    ua = (user_agent or "").strip()
    low = ua.lower()

    if not ua:
        return {"device_type": "unknown", "browser": "unknown", "operating_system": "unknown"}

    if any(token in low for token in ("ipad", "tablet", "playbook", "silk", "kindle")):
        device_type = "tablet"
    elif any(token in low for token in ("mobile", "mobi", "iphone", "android", "windows phone")):
        device_type = "mobile"
    else:
        device_type = "desktop"

    if "edg/" in low or "edge/" in low:
        browser = "Edge"
    elif "opr/" in low or "opera" in low:
        browser = "Opera"
    elif "firefox/" in low:
        browser = "Firefox"
    elif "chrome/" in low or "crios/" in low:
        browser = "Chrome"
    elif "safari/" in low:
        browser = "Safari"
    elif "curl/" in low:
        browser = "curl"
    elif "python" in low or "urllib" in low or "httpx" in low or "aiohttp" in low:
        browser = "Python client"
    elif "postman" in low:
        browser = "Postman"
    else:
        browser = "Other"

    if "windows" in low:
        os_name = "Windows"
    elif "android" in low:
        os_name = "Android"
    elif "iphone" in low or "ipad" in low or "ipod" in low:
        os_name = "iOS"
    elif "mac os" in low or "macintosh" in low:
        os_name = "macOS"
    elif "cros" in low:
        os_name = "ChromeOS"
    elif "linux" in low or "x11" in low:
        os_name = "Linux"
    else:
        os_name = "Other"

    return {"device_type": device_type, "browser": browser, "operating_system": os_name}


def init_schema() -> bool:
    """Create the logging schema and table once at startup (best effort)."""

    if not enabled():
        logger.info(
            "request logging disabled (LOG_ENABLED=%s, DATABASE_URL=%s)",
            LOG_ENABLED,
            "set" if DATABASE_URL else "missing",
        )
        return False
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=5) as conn:
            conn.execute(_DDL)
        logger.info("request logging ready: table '%s'", TABLE)
        return True
    except Exception as exc:  # noqa: BLE001 - logging must never break the app
        logger.warning("could not initialise request log table (%s)", exc)
        return False


def _worker_loop() -> None:
    connection = None
    retry_at = 0.0
    while True:
        entry = _QUEUE.get()
        if entry is None:
            break
        try:
            if time.monotonic() < retry_at:
                continue  # drop queued entries while the database is unavailable
            if connection is None or connection.closed:
                connection = psycopg.connect(DATABASE_URL, connect_timeout=5)
            connection.execute(_INSERT_SQL, tuple(entry.get(column) for column in INSERT_COLUMNS))
            connection.commit()
            retry_at = 0.0
        except Exception as exc:  # noqa: BLE001 - drop the entry, keep serving
            retry_at = time.monotonic() + 30.0
            logger.warning("request logging paused for 30s after database error: %s", exc)
            if connection is not None:
                try:
                    connection.rollback()
                    connection.close()
                except Exception:  # noqa: BLE001
                    pass
                connection = None
        finally:
            _QUEUE.task_done()
    if connection is not None:
        try:
            connection.close()
        except Exception:  # noqa: BLE001
            pass


def setup() -> None:
    """Prepare the schema and start the background writer thread."""

    global _WORKER
    if not enabled():
        init_schema()  # emits the disabled-config summary
        return
    if not init_schema():
        logger.warning("request logging unavailable; check DATABASE_URL and redeploy")
        return
    with _START_LOCK:
        if _WORKER is None or not _WORKER.is_alive():
            _WORKER = threading.Thread(target=_worker_loop, name="request-log-writer", daemon=True)
            _WORKER.start()


def log_request(entry: Dict[str, Any]) -> None:
    """Queue one request entry for background insertion. Never raises."""

    if not enabled() or _WORKER is None or not _WORKER.is_alive():
        return
    record = dict(_BLANK_ENTRY)
    record.update({key: value for key, value in entry.items() if key in _BLANK_ENTRY})
    try:
        _QUEUE.put_nowait(record)
    except queue.Full:
        logger.warning("request log queue is full - entry dropped")
