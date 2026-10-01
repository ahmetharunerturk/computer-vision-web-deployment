# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Ahmet Harun Ertürk
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    MODEL_PATH=/app/models/best.pt \
    MODELS_DIR=/app/models \
    LOG_ENABLED=false \
    DEMO_LOGIN_ENABLED=false

WORKDIR /app

# System libraries required by OpenCV and the image stack (no GUI in containers).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# CPU-only PyTorch keeps the image small and boots fast on typical Dokploy hosts.
# For GPU hosts, drop the --index-url flag (or point it at the matching CUDA index).
RUN pip install --upgrade pip \
    && pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu \
    && pip install -r requirements.txt \
    && pip uninstall -y opencv-python \
    && pip install --force-reinstall --no-deps opencv-python-headless

# Model weights may be packaged under models/ or bind-mounted at runtime.
COPY . .
RUN mkdir -p /app/models

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD python -c "import json,os,sys,urllib.request;data=json.load(urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health',timeout=4));sys.exit(0 if data.get('model_loaded') else 1)"

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
