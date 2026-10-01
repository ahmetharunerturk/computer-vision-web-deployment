# Dokploy Dockerfile and ZIP deployment

For configuration details, usage, and troubleshooting, see [USAGE.md](USAGE.md).

1. From the project directory, run `./make_dokploy_zip.ps1 -WithoutModel` in PowerShell if you will mount a model from the server. Run `./make_dokploy_zip.ps1` if you placed a `.pt` file in the project root or `models/` and want it included in the ZIP. Both commands work when no model exists. The archive name is based on the project directory name: `<directory>-dokploy-code-only.zip` or `<directory>-dokploy.zip`.
2. For a code-only ZIP, put the model on the server. For example, a `deploy` user could store it at `/home/deploy/cv-models/yolo.pt`.
3. In Dokploy, create an **Application**. Select **Dockerfile** as the build type, set Dockerfile Path to `Dockerfile`, and Docker Context Path to `.`.
4. If the weights are on the server, add a **Bind Mount** in **Advanced → Volumes/Mounts**: Host Path `/home/deploy/cv-models` (replace with your actual host directory), Mount Path `/app/models`.
5. Set the environment variables in Dokploy, using your actual filename and credentials:

   ```ini
   PORT=8000
   MODEL_PATH=/app/models/yolo.pt
   MODELS_DIR=/app/models
   DEMO_LOGIN_ENABLED=false
   AUTH_USER=operator
   AUTH_PASS=replace-with-a-strong-password
   LOG_ENABLED=false
   ```

   The built-in `admin/pass` account works only when `DEMO_LOGIN_ENABLED=true`; use it only for local testing. If the weight file is at the ZIP root, use `MODEL_PATH=/app/<filename>.pt` instead.
6. Add your domain in **Domains** and set **Container Port = 8000**. A separate port publication in **Advanced → Ports** is unnecessary for the domain route.
7. Upload the archive through Dokploy's **Drag and Drop .zip** source and deploy. If upload automatically starts a deployment, save the mount and environment settings, then redeploy.
8. Check the logs for a loaded model. `https://YOUR_DOMAIN/health` should return HTTP 200 and `model_loaded: true`. Sign in at `/login`, then test a real image.

`MODEL_PATH` must point to the file **inside the container**. A missing bind mount or wrong filename makes `/health` return HTTP 503 and the Docker health check report unhealthy. A mismatched domain container port can produce a 502 response.

The ZIP includes `.env.example` and excludes the private `.env` file. It contains model weights only if they were explicitly placed in the project directory and the ZIP was built without `-WithoutModel`.

Dokploy references: [ZIP source](https://docs.dokploy.com/docs/core/providers), [Dockerfile build](https://docs.dokploy.com/docs/core/applications/build-type), [Bind Mount](https://docs.dokploy.com/docs/core/applications/advanced), [domain container port](https://docs.dokploy.com/docs/core/domains).
