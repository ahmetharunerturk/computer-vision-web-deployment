# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Ahmet Harun Ertürk
"""Build a Dokploy source bundle without reading local secrets; weights are optional."""

import os
import shutil
import sys
import zipfile
from pathlib import Path

SRC = Path(__file__).resolve().parent    # project root (this script lives here)
OUT_ROOT = SRC.parent / (SRC.name + "-deploy")
ZIP_PATH = SRC.parent / (SRC.name + "-deploy.zip")
TREE = OUT_ROOT / SRC.name

TOP_FILES = [
    "main.py", "db.py", "detectors.py", "branding.py", "requirements.txt",
    "Dockerfile", ".dockerignore", ".env.example", "README.md", "LICENSE",
    "USAGE.md", "DOKPLOY.md",
]
BLOCKED_NAMES = frozenset(
    name.strip().lower() for name in os.environ.get("BLOCKED_WEIGHTS", "").split(",")
    if name.strip()
)


def discover_models() -> list[Path]:
    """Include optional project-local weights, never an arbitrary external path."""
    found: dict[str, Path] = {}
    for folder in (SRC, SRC / "models"):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.pt")):
            if path.name.lower() in BLOCKED_NAMES:
                print("skip (blocked):", path.name)
                continue
            if path.name in found and found[path.name] != path:
                raise SystemExit(f"duplicate model filename: {path.name}")
            found[path.name] = path
    return list(found.values())

def deploy_md(shipped: list) -> str:
    """README dropped into the bundle; weights are optional."""
    blocked = ", ".join(sorted(BLOCKED_NAMES)) or "(none)"
    model_lines = "\n".join("      models/" + m.name for m in shipped) or "      models/           add your YOLO detection .pt files here"
    return """# {app_name} - deployment bundle

Generated from the project sources. This app accepts Ultralytics YOLO detection .pt weights.
Blocked weight filenames (BLOCKED_WEIGHTS: {blocked}) are refused at load time.

## Layout
    {prefix}/
      main.py            web app (uvicorn main:app)
      branding.py        project identity / naming variables (env-driven)
      api/server.py      standalone JSON inference service (uvicorn api.server:app)
      detectors.py       shared YOLO detection backend
      db.py              optional PostgreSQL request logging
{model_lines}
      static/ templates/ web UI assets
      docs/images/       README screenshots
      requirements.txt   application dependencies
      Dockerfile         python:3.11-slim, CPU torch index, healthcheck on /health
      .env.example       configuration template (no secrets in this bundle)
      LICENSE            GNU AGPL-3.0 license

## Dokploy
Create an application with this Dockerfile (build context = {prefix}/).
Set MODEL_PATH=/app/models/<filename>.pt, MODELS_DIR=/app/models, PORT=8000,
AUTH_USER and AUTH_PASS in Dokploy Environment. Set the domain container port
to 8000. If the bundle contains no weights, bind-mount a host model directory
at /app/models before deployment. /health reports whether the model loaded.

## Models
Add Ultralytics YOLO detection weights to models/ before bundling, or mount
them at runtime. Every *.pt in MODELS_DIR appears in the model picker.
Set MODEL_PATH to the exact container path of the default model.

## Standalone API (optional)
    uvicorn api.server:app --host 0.0.0.0 --port 8000
    POST /predict   multipart field "image", form fields "confidence", "model"
    GET  /health, GET /models
""".format(
        app_name=os.environ.get("APP_NAME") or "Object Detection",
        prefix=SRC.name,
        blocked=blocked,
        model_lines=model_lines,
    )


def fresh(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


def main() -> int:
    if not SRC.exists():
        print("missing source", SRC)
        return 1
    fresh(OUT_ROOT)
    (TREE / "api").mkdir(parents=True)
    (TREE / "models").mkdir(parents=True)

    for name in TOP_FILES:
        shutil.copy2(SRC / name, TREE / name)
        print("+", name)
    shutil.copy2(SRC / "api" / "server.py", TREE / "api" / "server.py")
    print("+ api/server.py")

    for d in ("static", "templates", "docs"):
        shutil.copytree(SRC / d, TREE / d,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
        n = sum(1 for _ in (TREE / d).rglob("*") if _.is_file())
        print("+ %s/ (%d files)" % (d, n))

    models = discover_models()
    for src_path in models:
        dst = TREE / "models" / src_path.name
        shutil.copy2(src_path, dst)
        print("+ models/%s (%.1f MB)" % (src_path.name, dst.stat().st_size / 1e6))
    if not models:
        print("No weights included; mount models/ and set MODEL_PATH at deployment.")
    (TREE / "DEPLOY.md").write_text(deploy_md(models), encoding="utf-8")
    print("+ DEPLOY.md")

    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    packed = zipped = 0
    with zipfile.ZipFile(ZIP_PATH, "w") as zf:
        for path in sorted(TREE.rglob("*")):
            if not path.is_file():
                continue
            arc = SRC.name + "/" + path.relative_to(TREE).as_posix()
            big = path.suffix.lower() == ".pt"
            method = zipfile.ZIP_STORED if big else zipfile.ZIP_DEFLATED
            zf.write(path, arc, compress_type=method)
            packed += 1
            zipped += path.stat().st_size
            print("z:", arc)
    zip_size = ZIP_PATH.stat().st_size
    print("files=%d raw=%.1f MB zip=%.1f MB -> %s" % (packed, zipped / 1e6, zip_size / 1e6, ZIP_PATH))
    print("extracted tree ->", TREE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
