#!/bin/sh
# Backend container entrypoint.
#
# Creates the schema on a FRESH database only. A database that already
# exists is never migrated here: a container restart at 3am must not be
# able to change the schema of a database full of labelling, with no
# backup in the path. When the code moves ahead of an existing database
# the UI shows a banner and offers the upgrade, which takes a backup
# first (app/services/schema_state.py). See deploy/README.md.
set -eu

DATA_DIR="${ANPR_DATA_DIR:-/app/backend/data}"
mkdir -p "$DATA_DIR" "${ANPR_WORKSPACE_ROOT:-$DATA_DIR/workspace}" \
         "${ANPR_MODEL_WEIGHTS_DIR:-$DATA_DIR/models}" "${ANPR_JOBS_DIR:-$DATA_DIR/jobs}" \
         "${HOME:-$DATA_DIR/home}"
# Ultralytics only uses YOLO_CONFIG_DIR if it already exists and is
# writable; otherwise it silently falls back to /tmp.
[ -n "${YOLO_CONFIG_DIR:-}" ] && mkdir -p "$YOLO_CONFIG_DIR"
[ -n "${MPLCONFIGDIR:-}" ] && mkdir -p "$MPLCONFIGDIR"

if [ -z "${ANPR_DATABASE_URL:-}" ] && [ ! -e "$DATA_DIR/app.db" ]; then
  echo "entrypoint: no database at $DATA_DIR/app.db - creating it (alembic upgrade head)"
  alembic upgrade head
else
  echo "entrypoint: database exists - not migrating (the UI offers the upgrade, with a backup, if it is behind)"
fi

exec "$@"
