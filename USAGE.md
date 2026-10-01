# Usage and Dokploy deployment guide

This app runs **Ultralytics YOLO object detection (`detect`) `.pt` models** supplied by the operator. Users upload JPG, PNG, or WEBP images; the app draws bounding boxes and class labels on the image and shows a table of classes, confidence scores, and coordinates. Classification, segmentation, pose, and other `.pt` architectures require a different adapter.

## 1. Prepare a model

1. Choose an Ultralytics YOLO **detection** checkpoint, such as `yolo.pt` or `best.pt`.
2. Record its exact filename and location. `MODEL_PATH` must be the full **path inside the container** when running in Docker.
3. For large weights, keep the file in a host directory and mount that directory at `/app/models` instead of including it in the Dokploy ZIP. Any `.pt` filename can be used.

Only load checkpoints from sources you trust. PyTorch model files can execute code while loading.

## 2. Configure login

| Configuration | Result |
| --- | --- |
| `DEMO_LOGIN_ENABLED=false` with both `AUTH_USER` and `AUTH_PASS` set | Only the configured account can sign in. Use this for an internet-facing deployment. |
| `DEMO_LOGIN_ENABLED=true` | Enables the built-in `admin/pass` demo account. The separately configured account also works. Use only for local testing. |
| Demo disabled and either credential missing | Login is unavailable; submitting `/login` returns HTTP 503. |

The true values for the demo switch are `true`, `1`, `yes`, and `on`, case-insensitively. Other values disable it. Restarting after changing credentials or the demo setting invalidates previous sessions.

Even if `.env` contains `AUTH_USER=admin` and `AUTH_PASS=pass`, that pair works **only** when `DEMO_LOGIN_ENABLED=true`. Deployment ZIP files exclude `.env`, and the Docker default keeps the demo account disabled.

## 3. Run locally (optional)

1. Copy `.env.example` to `.env`.
2. Put a detection model in `models/`; create the directory if needed.
3. Set `MODEL_PATH=./models/yolo.pt`, `MODELS_DIR=./models`, and `PORT=8000` in `.env`, using the real model filename. Set `AUTH_USER` and `AUTH_PASS`, or enable the demo account only for local testing.
4. With Python 3.11, install CPU PyTorch and the other dependencies as shown in the [Dockerfile](Dockerfile).
5. Run `uvicorn main:app --host 0.0.0.0 --port 8000` from the project directory.
6. Confirm `http://localhost:8000/health` returns `model_loaded: true`, then open `http://localhost:8000/login`.

## 4. Build a Dokploy ZIP

In PowerShell, change to your project directory and choose a package:

```powershell
cd .\computer-vision-web-deployment
.\make_dokploy_zip.ps1 -WithoutModel  # mount a model directory from the server
.\make_dokploy_zip.ps1                # include project-local .pt files, if any
```

Run **one** of the two package commands for the archive you need. The script names its output from the current project directory, for example `computer-vision-web-deployment-dokploy-code-only.zip` or `computer-vision-web-deployment-dokploy.zip`. A ZIP without weights is valid. Both archives put `Dockerfile` and `.env.example` at the root and exclude private `.env` files. If a weight file is packaged under `models/yolo.pt`, its container path is `/app/models/yolo.pt`; a file at the ZIP root becomes `/app/yolo.pt`.

## 5. Upload a model to the server (code-only ZIP)

Replace `deploy`, `SERVER_IP`, and the local file path with your own values:

```powershell
ssh deploy@SERVER_IP "mkdir -p ~/cv-models"
scp "C:\path\to\yolo.pt" deploy@SERVER_IP:~/cv-models/yolo.pt
ssh deploy@SERVER_IP "ls -lh ~/cv-models/yolo.pt"
```

For this example, `/home/deploy/cv-models` is the **host path**. Bind-mount it at `/app/models` in the container. Use the actual absolute host path for your server account.

## 6. Configure the Dokploy application

1. Create an **Application** in Dokploy. Select **Dockerfile** as Build Type. Set Dockerfile Path to `Dockerfile` and Docker Context Path to `.`.
2. If the model is on the server, add a **Bind Mount** in **Advanced → Volumes/Mounts**: Host Path `/home/deploy/cv-models` (or your real host directory), Mount Path `/app/models`. Skip this when the model is packaged inside `models/` in the ZIP.
3. Set the environment variables below. Replace `yolo.pt` and the credentials with your values:

   ```ini
   PORT=8000
   MODEL_PATH=/app/models/yolo.pt
   MODELS_DIR=/app/models
   CONFIDENCE=0.60
   DEMO_LOGIN_ENABLED=false
   AUTH_USER=operator
   AUTH_PASS=replace-with-a-strong-password
   LOG_ENABLED=false
   ```

   If the model is at the ZIP root, set `MODEL_PATH=/app/<filename>.pt`. The `admin/pass` demo account can be enabled with `DEMO_LOGIN_ENABLED=true` for local testing.
4. Add your domain in **Domains**. Set **Container Port = 8000** and Path = `/`. If you change `PORT`, change the domain container port to match. No separate **Advanced → Ports** publication is needed for a domain route.
5. Upload the ZIP with **Drag and Drop .zip** and deploy. If upload starts a deployment automatically, save the mount and environment settings, then redeploy.

## 7. Verify and use the deployment

1. Check Dokploy logs for `Uvicorn running on http://0.0.0.0:8000` and `Detection model loaded from ...`.
2. Open `https://YOUR_DOMAIN/health`. A ready app returns HTTP 200 with `model_loaded: true`. A missing or incompatible model returns HTTP 503 and an `error` field.
3. Sign in at `/login` with the configured account.
4. Upload or drag in a JPG, PNG, or WEBP image. The interface shows the annotated image, detection count, class names, and scores.
5. If `MODELS_DIR` contains multiple `.pt` files, select another weight file from the model picker. `LABEL_ALIASES=raw_name=Display Name` changes class names shown in the interface.

## 8. Update a model and troubleshoot

- Replacing the file at `MODEL_PATH` causes the app to detect the change and reload the model. If the filename changes, update `MODEL_PATH` in Dokploy Environment and redeploy.
- **502 Bad Gateway:** Check that Uvicorn started and that the domain **Container Port** matches `PORT`.
- **`/health` 503 / `Model file not found`:** Check the host file, bind mount paths, and exact filename in `MODEL_PATH`.
- **`/health` 503 / incompatible model:** Confirm the weight is an Ultralytics YOLO **detection** `.pt` checkpoint.
- **`cv-deploy.db` / `ConnectionTimeout`:** The app cannot reach the PostgreSQL server in `DATABASE_URL`. Set `LOG_ENABLED=false` and redeploy if request logging is unnecessary. Otherwise, use Dokploy's **Internal Connection URL** and ensure the app can reach the database on its internal network. This is separate from a model-related `/health` 503 response.
- **Login returns 503:** Set both `AUTH_USER` and `AUTH_PASS`, or enable the demo account for local testing.
- **Request logging:** Set `LOG_ENABLED=true` and provide a working `DATABASE_URL`. Logging is disabled by default.

## Other settings and API

`UPLOAD_MAX_MB` limits upload size (default 10 MB), and `MAX_IMAGE_PIXELS` limits decoded image area (default 25 million pixels). `APP_NAME`, `APP_TAGLINE`, `UNIT_SINGULAR`, `UNIT_PLURAL`, `PAGE_SUBTITLE`, and `LOGIN_SUBTITLE` customize interface text. `BLOCKED_WEIGHTS` hides specified `.pt` filenames from the model picker.

`GET /models` lists selectable weights. `POST /predict` accepts a multipart `image` file and optional `model` and `confidence` fields; it returns detections and a base64 encoded annotated image. The Dockerfile serves `main:app`. The optional `api.server:app` serves JSON detections only and has no login gate; protect it separately if exposed on a network.

Dokploy references: [ZIP source](https://docs.dokploy.com/docs/core/providers), [Dockerfile build](https://docs.dokploy.com/docs/core/applications/build-type), [Bind Mount](https://docs.dokploy.com/docs/core/applications/advanced), [domain container port](https://docs.dokploy.com/docs/core/domains), [database internal connection](https://docs.dokploy.com/docs/core/databases/connection).
