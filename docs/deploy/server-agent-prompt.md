# Prompt for the agent on the production server

Copy everything below the line and give it to the Claude agent running on the
Ubuntu GPU server, in a session whose working directory is the repository root.

---

You are working on **ANPR-ATCC Dataset Studio**, an existing, working application.
It currently runs as an Electron desktop app on Windows. Your job is to **deploy it
as a web app on this Ubuntu server on port 8080**, using Docker, nginx and the
server's dedicated GPU — **without changing how the application behaves**.

Read this whole brief before you touch anything. The constraints are not
decoration; several of them exist because of bugs this project has already been
through.

## 1. What the app is

- **Backend**: FastAPI + SQLAlchemy + SQLite, in `backend/`. Entry point is
  `app.main:app`. Health check is `GET /health`.
- **Frontend**: React + TypeScript + Vite, in `desktop/`. Build output is
  `desktop/dist/`. The renderer never touches the Electron bridge (the preload
  exposes `window.anprAtcc`, and nothing in `desktop/src/` reads it), so the same
  source builds as a plain web app.
- **Electron**: `desktop/electron/` — main process + preload. **Not part of this
  deployment.** Leave it working; do not delete it.
- **ML**: Ultralytics YOLO for detection and training, `trackers` for ByteTrack,
  `rapidocr` + `onnxruntime` for plate OCR. Torch is pinned to `2.14.0` /
  torchvision `0.29.0` in `backend/pyproject.toml`.

Read `CONTEXT.md` at the repo root first — it is the domain model, and the words
in it (source, run, track, frame candidate, dataset version) mean specific things.

## 2. What you must deliver

1. A **GPU-enabled backend container** running uvicorn.
2. An **nginx container** serving the built frontend and proxying the API, listening
   on **host port 8080**.
3. A **docker compose file** wiring them together with persistent volumes.
4. A short **deployment README** with the exact commands to start, stop, update and
   back up.
5. A report of what you verified, and what you could not.

Put every new file under `deploy/` (and `docker-compose.yml` at the repo root if you
prefer it there). **Create new files; do not restructure existing ones.**

## 3. Hard constraints — do no harm

These are non-negotiable. If one of them blocks you, stop and report rather than
working around it.

- **Do not change backend application code.** The backend is already fully
  configurable through `ANPR_*` environment variables (see
  `backend/app/core/config.py`) and already handles POSIX process spawning
  (`backend/app/services/jobs/runner.py` branches on `sys.platform`). If you believe
  a backend code change is genuinely required, stop and explain why instead of
  making it.
- **Exactly one change is expected in existing frontend code**: `desktop/src/api.ts`
  line ~50, `const API_BASE = "http://127.0.0.1:8000"`. Make it read a build-time
  variable with the current value as the default, so the desktop build is
  byte-for-byte equivalent in behaviour:
  ```ts
  const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";
  ```
  The web image builds with `VITE_API_BASE=/api`. **The default must stay the
  absolute localhost URL** — the packaged desktop app loads from `file://`, where a
  relative URL resolves to nothing.
- **Never run `alembic upgrade` or `downgrade` against a database that holds real
  data without taking a backup first**, and never against a copy inside
  `backend/data/`. SQLite runs in WAL mode here, so `cp` of the `.db` file is lossy —
  use SQLite's backup API (`backend/app/services/schema_state.py` has `backup()`,
  which already does this correctly).
- **Run the test suite from `backend/` only**, on files under `backend/tests/`.
  `backend/tests/conftest.py` redirects the app at a temporary database, and a
  conftest only applies to files beneath it. A test or script run from anywhere else
  gets the **real** database. `config.py` now refuses that under pytest, but do not
  rely on it: any ad-hoc script must set `ANPR_DATABASE_URL` and
  `ANPR_WORKSPACE_ROOT` to disposable paths **before importing anything from `app`**.
- **Both suites must still pass when you are done**: `cd backend && pytest -q`
  (~874 tests) and `cd desktop && npm test` (~319 tests). Run them before you start
  too, so you know what you inherited.
- **Do not commit secrets** — no RTSP passwords, no basic-auth hashes, no `.env`
  with real values. Ship a `.env.example`.

## 4. Facts about this codebase you will need

### Configuration
All settings take an `ANPR_` prefix (`backend/app/core/config.py`). Defaults are
relative to the backend working directory:

| Variable | Default | What it is |
|---|---|---|
| `ANPR_DATABASE_URL` | `sqlite:///data/app.db` | The database |
| `ANPR_WORKSPACE_ROOT` | `data/workspace` | Per-project files: decoded frames, crops, exports |
| `ANPR_MODEL_WEIGHTS_DIR` | `data/models` | `.pt` weights plus `<id>.json` sidecars |
| `ANPR_JOBS_DIR` | `data/jobs` | Job progress files and worker logs |
| `ANPR_DEVICE` | `auto` | `auto` / `cpu` / `cuda:0` — see `app/ml/device.py` |

Point all of these at mounted volumes. **The workspace is the expensive one** — it
holds every decoded frame and every dataset export.

### How background work runs
`backend/app/services/jobs/runner.py` launches **detached child processes**
(`sys.executable -m app.services.jobs.worker <job_id>`) that outlive the request and
are meant to outlive the app. Consequences for your deployment:

- The worker must be able to import `app` and see the **same** database, workspace,
  models and jobs directories. Easiest correct answer: workers run **inside the same
  container** as the API. Do not try to split them out.
- Do not run uvicorn with multiple workers or `--reload`. On startup the app
  reconciles jobs (`reconcile_jobs`) and live captures; several processes doing that
  at once against one SQLite file is asking for trouble. **One uvicorn process.**
- A container restart kills detached workers. That is expected and handled —
  `reconcile_jobs` marks jobs whose process is gone as failed on next start.

### Live RTSP capture
`backend/app/services/rtsp_session.py` runs capture and processing on **threads
inside the API process**, and the session registry is process-local. So:

- RTSP cameras must be reachable **from this server**, not from the user's laptop.
  Confirm this early — if the camera is on a private network the user's Windows
  machine can see and this server cannot, the Live tab will not work and no amount
  of Docker configuration fixes it.
- Stopping the container stops any live capture. Again expected, and reconciled on
  restart.

### Endpoints nginx must not get in the way of
- `GET /processing-runs/{id}/rtsp/preview.jpg` — live preview, polled continuously.
  It returns a custom header **`X-Frame-Sequence`** that the UI reads
  (`desktop/src/components/LivePreview.tsx`). It must survive the proxy.
  Set `proxy_buffering off;` for this path or the preview lags behind by seconds.
- `GET /projects/{id}/sources/{id}/video` — `FileResponse`, which honours HTTP
  **Range** requests; the `<video>` element depends on that. Do not let the proxy
  strip or buffer it.
- `GET /dataset-versions/{id}/archive` — a **streamed zip**, deliberately never
  staged on disk. It can run for minutes on a large dataset, so raise
  `proxy_read_timeout` and keep buffering off for this path.
- `GET /frames/{id}/image`, `/frames/{id}/thumbnail`, `/tracks/frames/{id}/image` —
  image endpoints hit hundreds at a time by the contact sheet.

### GPU
- Torch's CUDA build **is not on PyPI**. `backend/requirements-gpu.txt` carries the
  index URL (`https://download.pytorch.org/whl/cu132`) and must be installed
  **before** `pip install -e .`, or pip resolves a `+cpu` wheel and everything
  silently runs on the CPU. This has happened to this project before. After
  building, verify inside the container:
  ```
  python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
  ```
  A `+cpu` suffix or `False` means the image is wrong. **Do not ship it.**
- The cu132 index may not publish wheels for every Python version. Check which
  Python versions it has wheels for and pick the base image accordingly — the
  project requires `>=3.11`. Do not assume.
- The host needs `nvidia-container-toolkit`. If it is not installed, install it and
  say so in the README.
- Use `opencv-python-headless` (already the dependency) — do not pull in GUI OpenCV.
- Only one training run is allowed at a time; that is enforced in the app because
  this machine has one GPU.

### Database schema on a fresh server
There is a schema-state service (`backend/app/services/schema_state.py`) and the UI
shows a banner when the database is behind. For a **fresh** deployment with no data,
`alembic upgrade head` from `backend/` is the right call. For a database that already
holds work, back it up with that service's `backup()` first.

## 5. The shape to build

```
browser :8080 ──► nginx ──► /        static files from desktop/dist
                        └─► /api/*   uvicorn (GPU container), prefix stripped
```

The frontend is a single page with no client-side router, so nginx needs
`try_files $uri /index.html;` and nothing cleverer.

Why `/api` rather than proxying at the root: the API's paths (`/projects`,
`/frames`, `/tracks`) would otherwise collide with static routes. Keep the API
under one prefix and strip it in the proxy.

Suggested files:

```
deploy/Dockerfile.backend     CUDA base, python, requirements-gpu.txt FIRST, then pip install -e .
deploy/Dockerfile.web         node build stage (VITE_API_BASE=/api) -> nginx stage
deploy/nginx.conf             static + /api proxy, with the streaming rules above
deploy/.env.example           ANPR_* and any auth settings, no real values
deploy/README.md              start / stop / update / back up / restore
docker-compose.yml            the two services, volumes, GPU reservation, restart policy
```

Notes for the web image build: `desktop/package.json`'s `build` script runs
`tsc -b && vite build`, and `desktop/vite.config.ts` includes `vite-plugin-electron`,
which builds the Electron main process too. That is harmless but pulls the Electron
binary during `npm ci`. Set `ELECTRON_SKIP_BINARY_DOWNLOAD=1` for the image build.
If you want to skip the Electron plugin entirely for the web build, you may gate it
behind an env var in `vite.config.ts` — but **the default path must stay exactly as
it is** so the desktop build is unaffected, and `npm test` must still pass.

Volumes (named volumes or bind mounts, your call — document which):

```
data        -> /app/backend/data           (the SQLite database)
workspace   -> /app/backend/data/workspace (frames, crops, exports - the big one)
models      -> /app/backend/data/models    (.pt weights + sidecars)
jobs        -> /app/backend/data/jobs      (progress files, worker logs)
incoming    -> /incoming  (read-only)      (videos the user scp's in - see below)
```

## 6. Things that will bite you if you do not plan for them

1. **There is no upload endpoint.** Video import takes a **server-side path**
   (`POST /projects/{id}/sources` with `{"path": "..."}`, see
   `backend/app/api/sources.py`), and the file is copied into the project workspace.
   On the desktop that was the user's own disk; here it is the server's. Mount an
   `/incoming` directory, read-only, and document that videos are `scp`'d there and
   referenced by that path in the UI. **Do not build an upload endpoint as part of
   this task** — it is separate work the user has not asked for yet. Just make the
   path route exist and say so in the README.
2. **There is no authentication at all.** Port 8080 on a reachable server means
   anyone who can reach it can delete projects and datasets. Add HTTP basic auth in
   nginx, with the password file mounted from outside the image and **not** in git.
   If the user has said they want it open, say clearly in your report that it is open.
3. **CORS is wide open** (`allow_origins=["*"]` in `backend/app/main.py`) because the
   desktop app is local-first. Once nginx serves the UI and the API on the same
   origin, this stops mattering for normal use — but note it in your report; do not
   silently "fix" it in backend code, that is a change the user has not approved.
4. **SQLite is a single writer.** Two or three people labelling at once is fine. Ten
   is not. Do not migrate to Postgres; just say so in the README if you see the
   limit mattering.
5. **Windows paths in an existing database.** If the user copies their Windows
   `app.db` to this server, every stored path in it (`frames.image_path`,
   `sources.path_or_uri`, workspace paths) is a `C:\...` string that means nothing
   here. **Deploy with a fresh, empty database** unless the user explicitly asks for
   a data migration — and if they do, that is its own task with its own backup.
6. **Image size.** CUDA + torch + ultralytics lands around 6–8 GB. Use a multi-stage
   build and a `.dockerignore` that excludes `backend/data/`, `backend/runs/`,
   `backend/.venv/`, `desktop/node_modules/`, `desktop/dist*/`, `desktop/release/`
   and `.git/`. Check before building: `backend/data/` on a working machine can be
   many gigabytes, and without a `.dockerignore` it all goes into the build context.
7. **Disk.** Frames and exports grow fast; the app already refuses to start a run
   when free space is low (`backend/app/services/run_estimate.py`). Make sure the
   workspace volume is on the big disk, not the root filesystem, and say in the
   README how to check.

## 7. Verify before you report success

Do not report "deployed" on the strength of a container that started. Check:

- [ ] `curl -sf http://localhost:8080/api/health` returns ok.
- [ ] `http://localhost:8080/` serves the UI, and the browser console shows no
      failed requests to `127.0.0.1:8000` (that would mean the build picked up the
      default API base).
- [ ] In the container:
      `python -c "import torch; print(torch.__version__, torch.cuda.is_available())"`
      prints a CUDA build and `True`.
- [ ] `nvidia-smi` works inside the container.
- [ ] Create a project through the UI, import a video from `/incoming`, run
      detection, and confirm the job finishes and tracks appear. This is the single
      most valuable check — it exercises the database, the workspace volume, the
      detached worker and the GPU in one go.
- [ ] Open a frame in the Label tab and confirm the image loads.
- [ ] Export a dataset version and download the ZIP through the browser; confirm it
      opens and `data.yaml` inside has **no** `path:` line.
- [ ] If an RTSP camera is reachable: start a live capture and confirm the preview
      updates smoothly (this is the `proxy_buffering off` check).
- [ ] `docker compose restart` and confirm the app comes back with its data intact.
- [ ] `cd backend && pytest -q` and `cd desktop && npm test` both pass.

## 8. Report back

Tell the user plainly:

- What you created, and the one-line change you made to `desktop/src/api.ts`.
- Each item in section 7: passed, failed, or not checked and why.
- Whether the GPU is genuinely being used, with the output you based that on.
- Whether RTSP cameras are reachable from this server.
- Whether the deployment is behind authentication or open.
- How to put videos on the server, in one sentence.
- Anything you found that this brief got wrong. It was written by reading the code
  on a Windows machine, not by running it here — if reality differs, reality wins,
  and the user needs to know.

Work in small commits with clear messages. If something in the brief conflicts with
what you find in the code, **follow the code and say so**.
