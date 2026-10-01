# Prompt for the agent on the production server

Copy everything below the line and give it to the Claude agent running on the
Ubuntu GPU server, in a session whose working directory is the repository root.

---

You are working on **ANPR-ATCC Dataset Studio**, an existing, working application.
It currently runs as an Electron desktop app on Windows. Your job is to **deploy it
as a web app on this Ubuntu server, served on port 80 at the domain
`studio.highwaynetra.in`**, using Docker, nginx and the server's dedicated GPU —
**without changing how the application behaves**.

The domain's DNS already points at this machine. That means **this is a public
address**: the moment it is up, anyone on the internet can reach it. Section 6.2 is
therefore not advice, it is part of the job.

Read this whole brief before you touch anything. The constraints are not
decoration; several of them exist because of bugs this project has already been
through.

### What is already true on that machine

Checked on the server, so do not re-derive it — but do re-check anything that looks
stale, and **tell the user if reality has moved**:

- **DNS**: `studio.highwaynetra.in` resolves to the server's public IP
  `160.187.179.196`. Confirmed.
- **GPU**: RTX 5070, 12 GB — **not** the GTX 1650 that this repo's older comments and
  planning docs assume. See the GPU notes below; this changes what a correct image
  looks like.
- **Docker**: Docker and Compose are installed, `nvidia-smi` works.
- **Ports 80 and 443 belong to another live system.** `/app/mlff-node` runs its own
  nginx, certbot, api-server and MySQL in containers. Its nginx is the
  `default_server`, which is why `studio.highwaynetra.in` currently shows the MLFF
  app. Studio cannot bind :80. See the co-tenancy rules below — this is the part of
  the job with the most to lose.
- **No nginx or certbot on the host itself** — MLFF's are inside containers, and its
  `conf.d` is regenerated from `/app/mlff-node/nginx/*.template` every six hours.
  Anything written straight into the generated config is gone within six hours;
  anything added to the templates survives.
- **No `backend/.venv` on that machine**, so the backend test suite has never run
  there. Create a venv (or run the suite inside the backend image) before you claim
  the tests pass; "could not run them" is an acceptable report, "they pass" without
  having run them is not.
- **Disk**: ~144 GB free, 84% used. Enough to start, not enough to ignore. The
  workspace volume is what grows.

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

The words in this project mean specific things — source, processing run, track,
frame candidate, dataset version. `CLAUDE.md` points at a `CONTEXT.md` for them, but
**that file has never existed**; the nearest thing is `docs/agents/domain.md` plus
the module docstrings, which are unusually thorough. Do not go looking for it.

## 2. What you must deliver

1. A **GPU-enabled backend container** running uvicorn.
2. An **nginx container** serving the built frontend and proxying the API, listening
   on **host port 80** with `server_name studio.highwaynetra.in;`.
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
  too, so you know what you inherited. Neither has ever run on that machine, so a
  failure you find at the start is probably environmental and **not** yours to fix
  as part of this task — report it and carry on.
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
- **`is_available()` is not enough on this machine.** The GPU here is an RTX 5070
  (Blackwell, compute capability sm_120) — not the GTX 1650 that the comments in
  `requirements-gpu.txt` and the planning docs were written against. A torch build
  without sm_120 kernels can still report `cuda.is_available() == True` and then
  fail, or fall back, the moment real work starts. So check the architecture list
  and actually run something:
  ```
  python -c "import torch; print(torch.__version__); print(torch.cuda.get_arch_list()); \
             print(torch.cuda.get_device_name(0)); \
             print((torch.randn(4096,4096,device='cuda')@torch.randn(4096,4096,device='cuda')).sum().item())"
  ```
  `sm_120` must appear in the arch list and the matmul must complete. Then run one
  real detection through the app and watch `nvidia-smi` show the process.
- The pinned index is `cu132`, i.e. **CUDA 13.2 — newer than the 12.8 that Blackwell
  needs**, so the existing pin is most likely correct as it stands. Verify rather
  than change it: if `cu132` genuinely has no sm_120 wheel for your Python version,
  report that and propose the change, do not silently edit the pins. They are pinned
  for a reason written at the top of the file.
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

**If you run the migration from the container's entrypoint, make it conditional.**
An entrypoint that migrates on every start is fine today, when the database is
empty, and is a liability the first time it restarts on top of real labelling —
nobody intends an unattended schema change at 3am, and there is no backup in that
path. Either migrate only when the database file does not exist yet, or put it
behind an env var that is off by default and documented in the README. The container
restarting must not be able to change the schema of a database full of work.

## 5. The shape to build

```
studio.highwaynetra.in ──► MLFF nginx :80 ──┐
                                            ├──► studio-web (own nginx)
http://<server-ip>:<port> ──────────────────┘      ├─► /        static from desktop/dist
                                                   └─► /api/*   uvicorn (GPU), prefix stripped
```

Studio does **not** bind :80. MLFF's nginx already owns it and is the
`default_server`.

### Three ways in, and auth has to cover all of them

The user wants Studio reachable **by domain, and by IP, and by port** — the last two
because the domain and its certificate are still settling. So:

- **By domain**: `studio.highwaynetra.in` through MLFF's nginx, as described below.
- **By IP and port**: publish studio-web's nginx on a host port as well
  (`0.0.0.0:<port>`, not just loopback — pick one and tell the user which). That
  port must be opened in the firewall; say so, because it will not work until it is.
- **By loopback**: `127.0.0.1:<port>` for your own testing before anything is public.

**Basic auth therefore belongs in Studio's own nginx config, not in MLFF's.** Put it
anywhere else and the IP:port route is an unauthenticated way straight past it. One
`auth_basic` covering `/` and `/api/` in Studio's own server block covers every route
in, whichever direction the request arrived from.

### HTTPS is not available yet — do not redirect to it

**Port 443 is closed at the firewall.** The user is opening it tomorrow. This
changes the order, and getting it wrong makes the site unreachable rather than
insecure:

- **Do not add an `80 → 443` redirect while 443 is closed.** Every request would be
  redirected to a port that refuses connections, and Studio would be unreachable by
  every route at once. This is exactly the trap in "serve HTTPS first" — the
  reasoning was right, the port is not open yet.
- **You can still obtain the certificate today.** The HTTP-01 challenge runs over
  :80, which is open, through MLFF's existing ACME path. Get it, confirm it, and
  leave it unused.
- **Serve plain HTTP for now, behind basic auth**, and say plainly in your report
  and in the README that **the password is travelling in the clear until 443 is
  open**. The user knows; write it down anyway.
- **Leave the switch ready.** The README must say exactly what to change tomorrow to
  turn HTTPS on — ideally one commented-out block or one `include` to uncomment,
  plus `nginx -t` and a reload — and how to confirm 443 is actually open first
  (`ss -lptn 'sport = :443'` plus a request from outside the machine).

### Co-tenancy rules — MLFF is live, and it is not ours

**The MLFF dashboard on :80 must keep running throughout.** The user has said this
directly: it is not to be stopped, restarted or interrupted at any point, not even
briefly. Studio runs *alongside* it, never instead of it. Reload only, after
`nginx -t`. If the only way you can see to make something work is to stop MLFF,
that is not a trade-off to weigh — stop and ask.

Name-based virtual hosting is what makes this safe: MLFF's nginx is the
`default_server`, so every request that does not match a specific `server_name` —
the server's IP, any other domain, the LiDAR devices — keeps going to MLFF exactly
as it does today. The Studio block matches one hostname and nothing else.

**Before you make the change, establish how MLFF is reached today** (the server's
IP, its own domain, or both) and write it down. After the reload, confirm that exact
path still works. Note that `studio.highwaynetra.in` currently shows MLFF only
because it falls through to the default server; that hostname was mapped for Studio,
so diverting it is the intended change, not a regression.

The change in `/app/mlff-node` is additive: one new server-block file, plus the
include line in each template that picks it up. Even so, treat every edit there as
touching production:

- **The Studio server block must never be `default_server`**, and must match only
  `server_name studio.highwaynetra.in;`. MLFF's LAN LiDAR devices post to
  `http://<ip>/api/` with no matching Host header, so they land on the default
  server. If Studio ever becomes the default — or matches on IP — those posts start
  hitting a FastAPI that knows nothing about them, and a live ingestion path breaks
  silently. **This is the single most dangerous thing in this task.**
- **`/api/` is a path both systems use.** Studio's `/api/` lives inside Studio's own
  server block only. Never add an `/api/` location to MLFF's default server.
- **After every reload, prove MLFF still works** before you call anything done:
  `curl -i http://<server-ip>/api/...` must still reach MLFF, and the MLFF app must
  still load on its own hostname. `nginx -t` passing only means the config parses.
- **Reload, never restart.** `nginx -s reload` after `nginx -t`, through MLFF's own
  script if it has one. Do not touch MLFF's compose file, containers or MySQL.
- **Keep a copy of the server block in this repo** (`deploy/mlff/studio.conf`) with a
  comment saying where it is installed. The config lives in another repo that will
  be pulled and redeployed by people who do not know Studio exists; when it
  disappears, the copy here is how it comes back.
- **Know the failure mode and say it out loud**: if the MLFF templates are ever
  replaced wholesale, the include lines go, and Studio becomes unreachable while
  MLFF carries on. That is the right way round, and the README should say what to
  re-add.

The frontend is a single page with no client-side router, so nginx needs
`try_files $uri /index.html;` and nothing cleverer.

Why `/api` rather than proxying at the root: the API's paths (`/projects`,
`/frames`, `/tracks`) would otherwise collide with static routes. Keep the API
under one prefix and strip it in the proxy.

Suggested files:

```
deploy/Dockerfile.backend     CUDA base, python, requirements-gpu.txt FIRST, then pip install -e .
deploy/Dockerfile.web         node build stage (VITE_API_BASE=/api) -> nginx stage
deploy/nginx.conf             server_name studio.highwaynetra.in, static + /api proxy,
                              basic auth, the streaming rules above, ACME challenge path
deploy/.htpasswd.example      shape only - the real one is generated on the server
deploy/.env.example           ANPR_* and any auth settings, no real values
deploy/README.md              start / stop / update / back up / restore / renew the cert
docker-compose.yml            the two services, volumes, GPU reservation, restart policy
```

Add `deploy/.htpasswd` and any `.env` holding real values to `.gitignore` in the
same commit that creates the examples, so the real file cannot be committed by
accident later.

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
2. **There is no authentication at all, and this address is public.** The app has no
   login, no sessions, no roles: every endpoint is open, including the ones that
   delete projects, sweep frames and remove dataset versions. On a desktop app that
   was fine — it only ever listened on localhost. On `studio.highwaynetra.in` it
   means a stranger with the URL can destroy months of labelling.

   So: **put HTTP basic auth in front of everything before the site is reachable**,
   not after. The password file is mounted from outside the image and never goes in
   git; ship only a `.htpasswd.example` and the `htpasswd` command to generate the
   real one.

   **Then add TLS.** Basic auth over plain HTTP sends the password in cleartext on
   every request, so HTTP + basic auth is barely better than nothing on a public
   domain. Use certbot (`certbot --nginx`, or the webroot challenge if nginx is in a
   container), redirect `:80` to `:443`, and leave `.well-known/acme-challenge/`
   reachable on `:80` so renewals work. If you cannot get a certificate — DNS not
   propagated, port 443 blocked — then **say so plainly in your report and tell the
   user the password is travelling in the clear** rather than leaving them to assume
   otherwise.

   Serving on port 80 is what was asked for and is the right first milestone. TLS is
   the second, in the same piece of work, not a "later".
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
7. **Disk, and it is shared with MLFF.** There is one filesystem on this machine, so
   Studio's workspace and MLFF's MySQL fill the same 144 GB. Frames and exports grow
   fast, and the app's own floor is **1 GB**
   (`SPACE_FLOOR_BYTES` in `backend/app/services/run_estimate.py`) — written for a
   laptop where filling the disk only hurt the person doing it. Here, Studio
   happily eating down to 1 GB free would take a live database with it.

   You cannot change that constant as part of this task — it is application code.
   What you can do: keep the bind mounts somewhere you can watch, put the `df` check
   and the numbers in the README, and **tell the user plainly** that the two systems
   share a disk and that Studio's built-in floor is too low to protect MLFF. If
   there is a second disk or a way to set a quota, say so; that is their call to
   make, not yours to implement unasked.

## 7. Verify before you report success

Do not report "deployed" on the strength of a container that started. Check:

- [ ] `curl -sf -u <user>:<pass> http://127.0.0.1:<port>/api/health` returns ok.
- [ ] **All three routes in work**, each of them with credentials and each of them
      401 without: the domain, `http://<server-ip>:<port>/`, and loopback. Test the
      IP route from another machine, not from the server — a port that answers
      locally may still be shut at the firewall.
- [ ] `curl -sI http://studio.highwaynetra.in/` reaches **this** server — check the
      DNS actually resolves here (`dig +short studio.highwaynetra.in`) rather than
      assuming it, and that the request lands on your nginx and not some other one.
- [ ] Without credentials, `curl -sI http://studio.highwaynetra.in/` returns **401**.
      Check a few API paths too, not just `/`: a basic-auth block that covers the
      static files and leaves `/api/` open is worse than none, because it looks safe.
- [ ] `http://studio.highwaynetra.in/` serves the UI in a browser, and the console
      shows no failed requests to `127.0.0.1:8000` (that would mean the build picked
      up the default API base).
- [ ] **Nothing redirects to HTTPS yet.** `curl -sI http://studio.highwaynetra.in/`
      must return 401, not 301 — a redirect to a closed port would make the site
      unreachable by every route at once.
- [ ] The certificate was obtained and `certbot renew --dry-run` passes, even though
      it is not in use yet.
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
- [ ] `docker compose restart` and confirm the app comes back with its data intact —
      and that the restart did **not** run a migration against a database with data
      in it.
- [ ] **MLFF is untouched**: its app still loads on its own hostname, and a
      LiDAR-shaped request to `http://<server-ip>/api/` still reaches MLFF, not
      Studio. Check this after every nginx reload, not once at the end.
- [ ] MLFF's dashboard still opens the way the user opens it — by the path you
      recorded before you touched anything, not by whatever path happens to work.
- [ ] Stop the Studio stack entirely and confirm MLFF's nginx still starts and
      serves. Studio being down must never take MLFF with it.
- [ ] `cd backend && pytest -q` and `cd desktop && npm test` both pass.

## 8. Report back

Tell the user plainly:

- What you created, and the one-line change you made to `desktop/src/api.ts`.
- Each item in section 7: passed, failed, or not checked and why.
- Whether the GPU is genuinely being used, with the output you based that on.
- Whether RTSP cameras are reachable from this server.
- Whether the site is behind basic auth, and whether it is on HTTPS or plain HTTP.
  If it is on plain HTTP, say in so many words that the password is sent in the
  clear on every request — do not let that be something the user discovers later.
- **The three addresses, written out and ready to use**: the domain, the IP and port,
  and the loopback one. Say which of them need a firewall port opened before they
  work.
- **Exactly what to change tomorrow to turn HTTPS on** once 443 is open, in two or
  three lines the user can follow without you.
- Where the basic-auth password file lives on this machine, and the command to add
  or change a user.
- How to put videos on the server, in one sentence.
- Anything you found that this brief got wrong. It was written by reading the code
  on a Windows machine, not by running it here — if reality differs, reality wins,
  and the user needs to know.

Work in small commits with clear messages. If something in the brief conflicts with
what you find in the code, **follow the code and say so**.
