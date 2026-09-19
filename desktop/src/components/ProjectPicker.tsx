import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { IconAlert, IconFolder, IconX } from "../Icons";
import { CLASS_PRESETS } from "../types";
import type { Project, ProjectContents } from "../types";

interface Props {
  onSelect: (project: Project) => void;
}

/** Bytes as something a person can weigh a decision against. */
function readableSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function ProjectPicker({ onSelect }: Props) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [newName, setNewName] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  // Chosen once, at creation: the preset is copied in, and from then on
  // the project owns its classes.
  const [preset, setPreset] = useState<string>(CLASS_PRESETS[0].id);
  /** The project the user is being asked to confirm deleting, if any. */
  const [doomed, setDoomed] = useState<Project | null>(null);
  const [contents, setContents] = useState<ProjectContents | null>(null);
  const [typedName, setTypedName] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    api
      .listProjects()
      .then(setProjects)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  async function askToDelete(project: Project) {
    setDoomed(project);
    setContents(null);
    setTypedName("");
    setError(null);
    setNotice(null);
    try {
      setContents(await api.getProjectContents(project.id));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  async function confirmDelete() {
    if (!doomed) return;
    setDeleting(true);
    setError(null);
    try {
      const removed = await api.deleteProject(doomed.id, typedName);
      setProjects((previous) => previous.filter((p) => p.id !== doomed.id));
      setNotice(
        `Deleted "${doomed.name}": ${removed.labels} label(s), ${removed.frames} frame(s), ` +
          `${removed.dataset_versions} dataset version(s).` +
          // Rows gone, files left. The user needs to know there is a
          // directory to clear by hand.
          (removed.workspace_removed ? "" : " Its files were left on disk - remove that folder by hand."),
      );
      setDoomed(null);
      setContents(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setDeleting(false);
    }
  }

  const busy = (contents?.running_jobs ?? 0) > 0;
  // Exactly, not case-insensitively and not trimmed. The point of
  // typing it is that it is deliberate.
  const nameMatches = doomed !== null && typedName === doomed.name;

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!newName.trim()) return;
    setCreating(true);
    setError(null);
    try {
      const project = await api.createProject(newName.trim(), preset);
      setNewName("");
      onSelect(project);
    } catch (e) {
      setError(String(e));
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="project-picker">
      <div className="project-picker-card">
        <div className="project-picker-brand">
          <span className="brand-icon">
            <IconFolder />
          </span>
          <h1>ANPR + ATCC Dataset Studio</h1>
        </div>
        <p className="project-picker-sub">
          Local-first dataset review and export for gantry ANPR/ATCC footage.
        </p>

        {error && (
          <p className="error" style={{ marginBottom: "1rem" }}>
            <IconAlert /> Could not reach the backend: {error}
          </p>
        )}

        {loading ? (
          <p className="empty">Loading projects…</p>
        ) : (
          <ul className="project-list">
            {projects.map((p) => (
              <li key={p.id}>
                <button onClick={() => onSelect(p)}>
                  {p.name}
                  <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>Open →</span>
                </button>
                {/* Its own button rather than anything inside the one
                    that opens the project: a click that reached both
                    would open the project it had just destroyed. */}
                <button
                  className="project-delete"
                  data-testid="delete-project"
                  aria-label={`Delete ${p.name}`}
                  title="Delete this project and everything in it"
                  onClick={() => askToDelete(p)}
                >
                  <IconX />
                </button>
              </li>
            ))}
            {projects.length === 0 && <li className="empty">No projects yet - create one below.</li>}
          </ul>
        )}

        {notice && (
          <p className="status" style={{ marginBottom: "0.6rem" }}>
            {notice}
          </p>
        )}

        {doomed && (
          <div className="delete-confirm">
            <p className="delete-confirm-title">
              Delete <strong>{doomed.name}</strong>?
            </p>
            {contents === null ? (
              <p className="empty">Working out what is in it…</p>
            ) : (
              <>
                <p className="delete-summary" data-testid="delete-summary">
                  This destroys {contents.labels} label(s), {contents.tracks} track(s), {contents.frames} frame(s)
                  across {contents.sources} source(s), {contents.dataset_versions} exported dataset version(s), and{" "}
                  {readableSize(contents.workspace_bytes)} of files. It cannot be undone.
                </p>
                {busy && (
                  <p className="error">
                    <IconAlert /> {contents.running_jobs} job(s) still running. Wait for them to finish, or cancel
                    them, before deleting this project.
                  </p>
                )}
              </>
            )}

            <label className="delete-confirm-name">
              Type the project name to confirm
              <input
                type="text"
                value={typedName}
                placeholder={doomed.name}
                onChange={(e) => setTypedName(e.target.value)}
              />
            </label>

            <div className="delete-confirm-actions">
              <button onClick={() => { setDoomed(null); setContents(null); }}>Cancel</button>
              <button className="btn-danger" disabled={!nameMatches || busy || deleting} onClick={confirmDelete}>
                {deleting ? "Deleting…" : "Delete this project"}
              </button>
            </div>
          </div>
        )}

        <form onSubmit={handleCreate} className="new-project-form">
          <input
            type="text"
            placeholder="New project name"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
          />
          <label className="sr-only" htmlFor="class-preset">
            Classes
          </label>
          <select
            id="class-preset"
            value={preset}
            onChange={(e) => setPreset(e.target.value)}
            title="Which classes this project starts with. You can edit them later."
          >
            {CLASS_PRESETS.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
          <button type="submit" className="btn-primary" disabled={creating || !newName.trim()}>
            Create
          </button>
        </form>
      </div>
    </div>
  );
}

export default ProjectPicker;
