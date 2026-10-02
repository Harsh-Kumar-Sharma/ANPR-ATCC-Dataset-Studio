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

# Ultralytics' weights_dir and runs_dir default to the bare "weights" and
# "runs", resolved against the cwd (/app/backend: root-owned, read-only for
# the container user). Every CUDA training run's AMP check downloads
# yolo26n.pt to weights_dir and failed with "Permission denied: 'weights'".
# Pin all three directories to the data volume so nothing resolves against
# the cwd and the AMP-check model is downloaded once, not on every run.
# Kept out of ANPR_MODEL_WEIGHTS_DIR so ultralytics' files never show up in
# the model picker. Written through ultralytics' own settings API, into the
# settings file in YOLO_CONFIG_DIR; only rewritten when a value differs.
ULTRALYTICS_DIR="$DATA_DIR/ultralytics"
mkdir -p "$ULTRALYTICS_DIR/weights" "$ULTRALYTICS_DIR/runs" "$ULTRALYTICS_DIR/datasets"
(cd "$DATA_DIR" && ULTRALYTICS_DIR="$ULTRALYTICS_DIR" python - <<'EOF'
import os

from ultralytics.utils import SETTINGS

root = os.environ["ULTRALYTICS_DIR"]
wanted = {name: os.path.join(root, name.removesuffix("_dir")) for name in ("weights_dir", "runs_dir", "datasets_dir")}
changed = {key: value for key, value in wanted.items() if SETTINGS.get(key) != value}
if changed:
    SETTINGS.update(changed)
    print(f"entrypoint: ultralytics directories set to {root}/: {', '.join(sorted(changed))}")
EOF
) || echo "entrypoint: WARNING - could not set ultralytics directories; training will fail its AMP check"

if [ -z "${ANPR_DATABASE_URL:-}" ] && [ ! -e "$DATA_DIR/app.db" ]; then
  echo "entrypoint: no database at $DATA_DIR/app.db - creating it (alembic upgrade head)"
  alembic upgrade head
else
  echo "entrypoint: database exists - not migrating (the UI offers the upgrade, with a backup, if it is behind)"
fi

exec "$@"
