import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { IconAlert, IconFolder, IconX } from "../Icons";
import { CLASS_PRESETS } from "../types";
import type { Project, ProjectContents } from "../types";

interface Props {
  onSelect: (project: Project) => void;
  /** Creating and deleting projects is an admin's; a user only opens
   *  the projects they have work in. */
  canManage?: boolean;
}

/** "3 labels", "1 source" - plural only when it needs to be. */
function count(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

/** What deleting destroys, as a sentence rather than a row of zeros.
 *
 *  Only what is actually there: a project with no labels and no exports
 *  should not make the reader parse "0 label(s), 0 exported dataset
 *  version(s)" to find the one number that matters. */
function describeContents(contents: ProjectContents): string {
  const parts = [
    contents.labels > 0 ? count(contents.labels, "label") : null,
    contents.tracks > 0 ? count(contents.tracks, "track") : null,
    contents.frames > 0 ? count(contents.frames, "frame") : null,
    contents.sources > 0 ? count(contents.sources, "source") : null,
    contents.dataset_versions > 0 ? count(contents.dataset_versions, "exported dataset version") : null,
    contents.workspace_bytes > 0 ? `${readableSize(contents.workspace_bytes)} of files` : null,
  ].filter((part): part is string => part !== null);

  if (parts.length === 0) return "There is nothing in this project yet.";
  const last = parts.pop() as string;
  const list = parts.length ? `${parts.join(", ")} and ${last}` : last;
  return `This destroys ${list}. It cannot be undone.`;
}

/** Bytes as something a person can weigh a decision against. */
function readableSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "an unknown amount";
  if (bytes < 1024) return `${bytes} B`;

  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }

  // Rounded before the unit is settled, not after. One byte short of a
  // megabyte is 1023.999 KB, which rounds to 1024 - and printing
  // "1024 KB" in the sentence someone is about to act on is the kind of
  // small wrongness that makes them doubt the rest of it.
  let shown = Number(value.toFixed(value < 10 ? 1 : 0));
  if (shown >= 1024 && unit < units.length - 1) {
    shown = 1;
    unit += 1;
  }
  return `${shown.toFixed(shown < 10 ? 1 : 0)} ${units[unit]}`;
}

function ProjectPicker({ onSelect, canManage = true }: Props) {
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
      const loaded = await api.getProjectContents(project.id);
      // Only if this is still the project being asked about. Clicking
      // one project then another before the first reply lands would
      // otherwise show the first project's counts under the second
      // one's name, on the most destructive confirmation in the app.
      setDoomed((current) => {
        if (current?.id === project.id) setContents(loaded);
        return current;
      });
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
                <button className="project-open" onClick={() => onSelect(p)}>
                  <span className="project-open-name" title={p.name}>
                    {p.name}
                  </span>
                  <span className="project-open-hint">Open →</span>
                </button>
                {/* Its own button rather than anything inside the one
                    that opens the project: a click that reached both
                    would open the project it had just destroyed. */}
                {canManage && (
                  <button
                    className="project-delete"
                    data-testid="delete-project"
                    aria-label={`Delete ${p.name}`}
                    title="Delete this project and everything in it"
                    onClick={() => askToDelete(p)}
                  >
                    <IconX />
                  </button>
                )}
              </li>
            ))}
            {projects.length === 0 && (
              <li className="empty">
                {canManage ? "No projects yet - create one below." : "No work has been given to you yet. Ask an admin."}
              </li>
            )}
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
              // Until this arrives the dialog cannot say what is about
              // to go, and the confirm button below stays disabled -
              // that is the whole reason the endpoint exists.
              <p className="empty">{error ? "Could not read what is in this project." : "Working out what is in it…"}</p>
            ) : (
              <>
                <p className="delete-summary" data-testid="delete-summary">
                  {describeContents(contents)}
                </p>
                {busy && (
                  <p className="error">
                    <IconAlert /> {contents.running_jobs} unfinished job(s) or live capture(s). Wait for them to
                    finish, cancel them, or stop the capture, before deleting this project.
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
              <button
                className="btn-danger"
                disabled={contents === null || !nameMatches || busy || deleting}
                onClick={confirmDelete}
              >
                {deleting ? "Deleting…" : "Delete this project"}
              </button>
            </div>
          </div>
        )}

        {canManage && (
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
        )}
      </div>
    </div>
  );
}

export default ProjectPicker;
