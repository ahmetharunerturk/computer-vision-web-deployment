# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Ahmet Harun Ertürk
"""Verify the optional-weight bundle has no secrets and compiles."""
import os
import py_compile
import zipfile
from pathlib import Path

SRC = Path(__file__).resolve().parent                       # project root
OUT_ROOT = SRC.parent / (SRC.name + "-deploy")
ZIP_PATH = SRC.parent / (SRC.name + "-deploy.zip")
ROOT = OUT_ROOT / SRC.name                                  # extracted app tree
PREFIX = SRC.name                                           # zip member prefix

blocked_names = frozenset(
    name.strip().lower() for name in os.environ.get("BLOCKED_WEIGHTS", "").split(",")
    if name.strip()
)

print("== zip members ==")
with zipfile.ZipFile(ZIP_PATH) as zf:
    names = [zi.filename for zi in zf.infolist()]
    for zi in zf.infolist():
        method = "STORED" if zi.compress_type == zipfile.ZIP_STORED else f"DEFLATED({zi.compress_type})"
        big = " <-- must be STORED" if zi.filename.endswith(".pt") and zi.compress_type != zipfile.ZIP_STORED else ""
        print(f"{method:>10}  {zi.file_size:>10} -> {zi.compress_size:>10}  {zi.filename}{big}")
        if zi.filename.endswith(".pt"):
            assert zi.compress_type == zipfile.ZIP_STORED, f"{zi.filename} not STORED!"
assert f"{PREFIX}/.env.example" in names, "configuration template missing"
assert f"{PREFIX}/.env" not in names, "private .env must not be packaged"
assert f"{PREFIX}/USAGE.md" in names, "deployment guide missing"
assert f"{PREFIX}/DOKPLOY.md" in names, "Dokploy guide missing"
assert f"{PREFIX}/LICENSE" in names, "license missing"
for screenshot in ("sign-in.png", "drop.png", "bird.png"):
    assert f"{PREFIX}/docs/images/{screenshot}" in names, f"screenshot missing: {screenshot}"

print("\n== shipped models ==")
shipped = [n for n in names if n.startswith(f"{PREFIX}/models/") and n.endswith(".pt")]
for name in shipped:
    print("OK   ", name)
bad = [n for n in shipped if Path(n).name.lower() in blocked_names]
assert not bad, f"blocked weights must NOT ship (BLOCKED_WEIGHTS): {bad}"
print("OK    no blocked weights in the bundle")

print("OK    .env.example included; .env excluded")

print("\n== compile extracted tree ==")
targets = ["main.py", "db.py", "detectors.py", "branding.py", "api/server.py"]
for rel in targets:
    p = ROOT / rel
    py_compile.compile(p, doraise=True)
    print("OK  ", rel)

print("\nALL BUNDLE CHECKS PASSED")
