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
