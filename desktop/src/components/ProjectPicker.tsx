import { useEffect, useState } from "react";
import { api } from "../api";
import { IconAlert, IconFolder } from "../Icons";
import type { Project } from "../types";

interface Props {
  onSelect: (project: Project) => void;
}

function ProjectPicker({ onSelect }: Props) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [newName, setNewName] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    api
      .listProjects()
      .then(setProjects)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!newName.trim()) return;
    setCreating(true);
    setError(null);
    try {
      const project = await api.createProject(newName.trim());
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
              </li>
            ))}
            {projects.length === 0 && <li className="empty">No projects yet - create one below.</li>}
          </ul>
        )}

        <form onSubmit={handleCreate} className="new-project-form">
          <input
            type="text"
            placeholder="New project name"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
          />
          <button type="submit" className="btn-primary" disabled={creating || !newName.trim()}>
            Create
          </button>
        </form>
      </div>
    </div>
  );
}

export default ProjectPicker;
