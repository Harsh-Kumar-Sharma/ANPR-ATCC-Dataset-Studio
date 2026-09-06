# Backend

FastAPI backend for the ANPR + ATCC Dataset Studio. See
[`../docs/03_TRD.md`](../docs/03_TRD.md) and
[`../docs/05_DATABASE_DESIGN.md`](../docs/05_DATABASE_DESIGN.md).

## Setup

```bash
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"
```

## Run migrations

```bash
./.venv/Scripts/python.exe -m alembic upgrade head
```

## Run the server

```bash
./.venv/Scripts/python.exe -m uvicorn app.main:app --reload
```

Health check: `GET http://127.0.0.1:8000/health`

## Run tests

```bash
./.venv/Scripts/python.exe -m pytest
```

## Configuration

Settings are read from environment variables prefixed with `ANPR_`
(see `app/core/config.py`), or a `.env` file in this directory.
`ANPR_DATABASE_URL` overrides the default SQLite database at
`data/app.db`.

## Adding a schema change

Edit/add a model under `app/db/models/`, then generate a migration:

```bash
./.venv/Scripts/python.exe -m alembic revision --autogenerate -m "describe the change"
```

## Building a standalone executable (for the desktop installer)

The desktop app's production build (`npm run dist` in `desktop/`)
bundles a standalone backend executable rather than requiring Python
to be installed separately. Build it first:

```bash
./.venv/Scripts/python.exe -m pip install pyinstaller
./.venv/Scripts/python.exe -m PyInstaller launcher.py \
  --name anpr-atcc-backend \
  --onedir \
  --noconfirm \
  --collect-all ultralytics \
  --collect-all torch \
  --collect-all torchvision \
  --collect-all onnxruntime \
  --collect-all rapidocr \
  --collect-all trackers \
  --collect-all supervision \
  --collect-all cv2 \
  --collect-all fastapi \
  --collect-all uvicorn
```

This produces `dist/anpr-atcc-backend/` (onedir, not onefile - more
reliable given how many native/dynamic libraries torch, opencv, and
onnxruntime bring in; onefile would re-extract all of that on every
launch, ~1.1GB here). `desktop/package.json`'s `build.extraResources`
points at this folder.

`launcher.py` (not `app/main.py`) is the packaged entry point: it
points data/workspace/model-weight paths at a per-user writable
location (`%LOCALAPPDATA%/ANPR-ATCC-Dataset-Studio` on Windows, not
next to the executable - an installed app is often not writable
without elevation), creates the database schema directly via
`Base.metadata.create_all()` (not Alembic - see the docstring in
`launcher.py` for why: Alembic's dynamic file-based loading of
`env.py`/revision files doesn't survive being frozen, confirmed by
actually running the packaged build and hitting
`ModuleNotFoundError: No module named 'app'`), then starts uvicorn
with the FastAPI app object passed directly (not the `"app.main:app"`
string form, which hits the same class of dynamic-import problem -
also confirmed by running it and seeing
`Could not import module "app.main"`). `electron/main.ts` spawns this
executable as a child process on launch and stops it on quit.

This build **was** verified end-to-end by actually running it: health
check, project creation, video import, and a full detect+track run
(loading `yolo26n.pt` through torch and running ByteTrack) all worked
from the packaged `.exe`, with data correctly landing under
`%LOCALAPPDATA%\ANPR-ATCC-Dataset-Studio\`. RapidOCR specifically
wasn't re-verified inside the frozen build (no track existed to run it
against in that quick pass) - see `docs/HANDOFF.md` for the exact
scope of what was and wasn't checked.
