# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Ahmet Harun Ertürk
"""Boot smoke: import the packaged app from the extracted bundle and sanity-check it.

Runs against <project>-deploy/<project>/ (the tree make_bundle.py produces).
Optionally proves that the branding variables reached the rendered HTML:

    python boot_smoke.py
    python boot_smoke.py --expect-brand "Object Detection"
"""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent                      # project root
TREE = SRC.parent / (SRC.name + "-deploy") / SRC.name       # extracted bundle app tree
sys.path.insert(0, str(TREE))  # win over the dev tree even though this script lives there

import main  # noqa: E402
import detectors  # noqa: E402
import db  # noqa: E402

assert str(main.__file__).lower().startswith(str(TREE).lower()), f"shadowed: {main.__file__}"
assert str(detectors.__file__).lower().startswith(str(TREE).lower()), f"shadowed: {detectors.__file__}"
assert str(db.__file__).lower().startswith(str(TREE).lower()), f"shadowed: {db.__file__}"

expect = None
if "--expect-brand" in sys.argv:
    expect = sys.argv[sys.argv.index("--expect-brand") + 1]

routes = getattr(main.app, "routes", [])
print("BOOT-IMPORT-OK from", main.__file__)
print("app routes:", len(routes))
print("default model:", main.MODEL_PATH.name)
print("models dir:", main.MODELS_DIR)
print("app name:", main.branding.APP_NAME)

# Render the real templates with the active UI context: proves the brand / unit
# strings flow into the login page and the main page (no HTTP server needed).
login_html = main.templates.env.get_template("login.html").render(error=None, **main.UI_CONTEXT)
index_html = main.templates.env.get_template("index.html").render(**main.UI_CONTEXT)
if expect is not None:
    assert expect in login_html, f"brand {expect!r} not rendered in /login HTML"
    assert expect in index_html, f"brand {expect!r} not rendered in / HTML"
    print("brand found in rendered login + index HTML")
print("BOOT-SMOKE-OK")
