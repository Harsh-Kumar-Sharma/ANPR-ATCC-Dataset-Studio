# Deploying Studio as a web app

The desktop app's backend and UI, served from the GPU server
(`160.187.179.196`) as a web app. The application is unchanged; only how
it is packaged and reached is new.

## How it fits together

```
http://studio.highwaynetra.in ──► MLFF's nginx :80 ─────┐   (Host header match only)
http://160.187.179.196:8090/  ──────────────────────────┼──► studio-web :8090  ── the app's own sign-in
http://127.0.0.1:8090/        ──────────────────────────┘      ├─ /        built UI
                                                               └─ /api/*   studio-backend (GPU), prefix stripped
```

- **studio-backend**: FastAPI, one uvicorn process, plus the detached job
  workers it spawns (detection, training, frame selection), all in one
  container on the GPU. No host port.
- **studio-web**: nginx, the built UI (`VITE_API_BASE=/api`) and the
  `/api` proxy. Published on host port **8090**. No basic auth: people
  sign in to the app itself, and the backend refuses the API without a
  session.
- **MLFF** (`/app/mlff-node`) is a separate live system that owns :80
  and :443. Its nginx proxies the one hostname `studio.highwaynetra.in` to
  port 8090. Every other request (the server's IP, LAN LiDAR devices,
  `mlff-node.highwaynetra.in`) goes to MLFF exactly as it did before.
  The Studio server block is kept in `deploy/mlff/` in this repo. See
  "The MLFF side" below.

| Address | Needs |
|---|---|
| `http://studio.highwaynetra.in/` | works now (port 80 is open) |
| `http://160.187.179.196:8090/` | **port 8090 opened at the firewall** |
| `http://127.0.0.1:8090/` | on the server only |
| `https://studio.highwaynetra.in/` | **port 443 opened at the firewall**, then "Turning HTTPS on" |

**Until HTTPS is on, passwords and sign-in tokens cross the network in
the clear.** Turn HTTPS on, then have everyone change their password.

## Files

| Path | What it is |
|---|---|
| `docker-compose.yml` | both services, volumes, GPU reservation |
| `deploy/Dockerfile.backend` | python 3.14 slim + cu132 torch (from `requirements-gpu.txt`, first) + the backend |
| `deploy/backend-entrypoint.sh` | creates the schema on a fresh database only, then starts uvicorn |
| `deploy/Dockerfile.web` | Node build of `desktop/` → nginx |
| `deploy/nginx.conf` | Studio's nginx: auth, static files, `/api` proxy, streaming rules |
| `deploy/mlff/` | the server block installed into MLFF's nginx (copy of record) |
| `deploy/.env.example` | template |

## Data

Everything lives in bind mounts on the host:

| Host | Container | Holds |
|---|---|---|
| `/app/studio/data` | `/app/backend/data` | `app.db` (SQLite), `workspace/` (frames, crops, exports: **the big one**), `models/`, `jobs/`, `backups/` |
| `/app/studio/incoming` | `/incoming` (read-only) | videos you copy onto the server to import |

The backend runs as host uid 1000 (`ctan`), so you can manage these files
without root.

### Putting videos on the server

Copy the file into `/app/studio/incoming/`, for example
`scp atcc1.mp4 ctan@<server>:/app/studio/incoming/`. Then in the Sources
panel enter `/incoming/atcc1.mp4`. Import copies it into the project
workspace, so you can delete it from `incoming` afterwards. There is no
browser upload yet; that is separate work.

### Disk: shared with MLFF

There is one filesystem. Studio's workspace and MLFF's MySQL fill the
same disk (about 144 GB free at deploy time). Studio refuses to start a
run only below **1 GB** free (`SPACE_FLOOR_BYTES` in
`backend/app/services/run_estimate.py`). That floor was written for a
laptop, and here it is far too low to protect MLFF's database. Watch it:

```bash
df -h /app                       # free space on the shared disk
du -sh /app/studio/data/*        # what Studio is holding
```

Use the app's Storage panel to free space. Moving `/app/studio/data` to
a second disk (if one is added) is just a change of `STUDIO_DATA_DIR`.

## Everyday commands

From the repo root on the server (`/home/ctan/ANPR-ATCC-Dataset-Studio`):

```bash
docker compose up -d --build        # start / update after a git pull
docker compose ps                   # status (backend should say healthy)
docker compose logs -f backend      # API + job logs
docker compose stop                 # stop Studio (MLFF unaffected)
docker compose restart              # restart Studio
```

Stopping or restarting kills running detection/training jobs and any
live capture. That is expected: on the next start they are marked
failed, not left "running" forever.

Job worker logs are in `/app/studio/data/jobs/`.

### Updating

```bash
git pull
docker compose up -d --build
```

The entrypoint **does not migrate an existing database**. A restart can
never change the schema of a database full of work. If an update brings
a schema change, the UI shows a "database is behind" banner and offers
the upgrade. That path takes a backup first (into
`/app/studio/data/backups/`). Only a missing `app.db` triggers
`alembic upgrade head` automatically.

### Backing up and restoring

SQLite runs in WAL mode, so **do not `cp` the `.db` while the app is
running**. Use SQLite's backup API:

```bash
docker compose exec backend python -c "from pathlib import Path; from app.services.schema_state import backup; print(backup(Path('/app/backend/data/app.db')))"
```

That writes `/app/studio/data/backups/app-<timestamp>.db`. Copy it, plus
`/app/studio/data/models/` (trained weights) and, if you want frames and
exports too, `/app/studio/data/workspace/` (large), somewhere off this
machine.

To restore: `docker compose stop`, then replace
`/app/studio/data/app.db` with the backup and delete any `app.db-wal`
and `app.db-shm` beside it. Then `docker compose up -d`.

## Signing in

People sign in to the app; there is no basic auth in front of it any
more. An **admin** manages users (account menu, top right → Manage
users) and hands out labelling **tasks**; a **user** sees only the
projects and sources given to them.

**First deploy with sign-in** (or any deploy onto a database with no
users): the database is behind, and nobody exists yet.

1. Open the site. The sign-in screen says the database needs updating;
   press **Update the database** in the banner. It backs up first, into
   `/app/studio/data/backups/`.
2. Reload. With nobody in the database, the screen offers **Create
   admin**. Do this straight away: until an admin exists, the first
   person to reach the site gets to be it.
3. Add everyone else from Manage users.

After that, updating the database (the banner, on later deploys) needs
an admin to be signed in.

Forgotten admin password, with no other admin to reset it: create a
new admin from the server.

```bash
docker compose exec backend python -c "from app.db.session import SessionLocal; from app.services.auth import create_user; db=SessionLocal(); print(create_user(db, 'rescue', 'change-me-now', 'Rescue', 'admin').username)"
```

## Turning HTTPS on (once 443 is open)

The certificate for `studio.highwaynetra.in` is already issued, in MLFF's
`/app/mlff-node/certbot/conf`. MLFF's certbot container renews it, and
MLFF's nginx re-reads it every six hours.

1. **Confirm 443 is really open from outside**, not just listening:
   `ss -lnt 'sport = :443'` on the server, then from **another machine**:
   `curl -skI https://studio.highwaynetra.in/` must get an HTTP response
   (a 200 from Studio after step 2, or MLFF's 404/200 before it), not a
   timeout. If it times out, stop here: a redirect to a closed port takes
   Studio off the air by every route.
2. Enable the 443 block, and test it **before** redirecting:
   ```bash
   cd /app/mlff-node/nginx/sites
   mv studio-https.conf.disabled studio-https.conf
   docker exec mlff-node-nginx sh /etc/nginx/mlff/start.sh reload   # runs nginx -t first
   curl -sI https://studio.highwaynetra.in/                         # expect 200 (from another machine)
   ```
3. Only then redirect :80 to :443. In `studio.conf` uncomment the line
   `# return 301 https://$host$request_uri;` and reload again with the
   same command. `curl -sI http://studio.highwaynetra.in/` should now
   be a 301 to https.
4. Have everyone change their password (they have been travelling in
   the clear).
5. Mirror the change in `deploy/mlff/` in this repo and commit it in
   both repos.

## The MLFF side

What was added to `/app/mlff-node` (to be committed in MLFF's repo):

- `nginx/sites/studio.conf`: `server_name studio.highwaynetra.in` on
  :80, proxying to `http://172.18.0.1:8090` (MLFF's Docker network
  gateway, i.e. the host). It is **never** `default_server` and never
  matches an IP, so LiDAR devices posting to `http://<ip>/api/` still
  reach MLFF.
- `nginx/sites/studio-proxy.inc`: the proxy settings, shared with
- `nginx/sites/studio-https.conf.disabled`: the 443 block, off.
- One line in **both** `nginx/http.conf.template` and
  `nginx/https.conf.template`:
  ```
  include /etc/nginx/mlff/sites/*.conf;
  ```

Rules for touching it: `nginx -t` and **reload only, never restart**,
through MLFF's own script:
`docker exec mlff-node-nginx sh /etc/nginx/mlff/start.sh reload`. Then
check MLFF still answers on `http://<server-ip>/api/health`.

**If Studio's domain stops working after an MLFF redeploy**, the templates
were probably replaced and the include line went with them. MLFF keeps
working and Studio is unreachable by domain only (IP:8090 still works).
To fix it, copy `deploy/mlff/*` back into `/app/mlff-node/nginx/sites/`,
re-add the include line to both templates, and reload.

If MLFF's Docker network is ever recreated on another subnet, the
gateway changes. Check it with
`docker network inspect mlff-node_default --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}'`
and update `studio-proxy.inc`.

## Known limits

- **SQLite is a single writer.** Two or three people labelling at once is
  fine. Around ten would start hitting lock waits.
- **Two roles only.** An admin can do everything, including deleting
  projects; a user labels the tasks given to them.
- **CORS is `*`** in `backend/app/main.py` (left over from the desktop
  design). The UI and API now share an origin, so it does not matter for
  normal use, but it has not been tightened.
- **RTSP cameras must be reachable from this server**, not from your
  laptop. The capture runs here.
- **One GPU**: one training run at a time (enforced by the app).
