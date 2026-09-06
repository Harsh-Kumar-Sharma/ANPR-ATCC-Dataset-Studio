import json
from datetime import datetime, timezone
from pathlib import Path

# Layout mirrors docs/05_DATABASE_DESIGN.md exactly - see "## Workspace".
_SUBDIRS = (
    "source",
    "cache/thumbnails",
    "derived/tracks",
    "derived/plates",
    "review",
    "exports",
    "logs",
)


def project_workspace_path(workspace_root: Path, project_id: str) -> Path:
    return workspace_root / project_id


def create_project_workspace(workspace_root: Path, project_id: str, project_name: str) -> Path:
    """Create the on-disk workspace tree and manifest for a new project.

    Idempotent: safe to call again for the same project_id (existing
    directories/files are left untouched, never overwritten).
    """
    root = project_workspace_path(workspace_root, project_id)
    for subdir in _SUBDIRS:
        (root / subdir).mkdir(parents=True, exist_ok=True)

    manifest_path = root / "project.json"
    if not manifest_path.exists():
        manifest = {
            "project_id": project_id,
            "name": project_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return root
