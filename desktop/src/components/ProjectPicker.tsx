import { useEffect, useState } from "react";
import { api } from "../api";
import type { Project } from "../types";

interface Props {
  onSelect: (project: Project) => void;
}

function ProjectPicker({ onSelect }: Props) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [newName, setNewName] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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
    try {
      const project = await api.createProject(newName.trim());
      setNewName("");
      onSelect(project);
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="project-picker">
      <h1>ANPR + ATCC Dataset Studio</h1>
      {error && <p className="error">Could not reach the backend: {error}</p>}
      {loading ? (
        <p>Loading projects...</p>
      ) : (
        <ul className="project-list">
          {projects.map((p) => (
            <li key={p.id}>
              <button onClick={() => onSelect(p)}>{p.name}</button>
            </li>
          ))}
          {projects.length === 0 && <li className="empty">No projects yet.</li>}
        </ul>
      )}
      <form onSubmit={handleCreate} className="new-project-form">
        <input
          type="text"
          placeholder="New project name"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
        />
        <button type="submit">Create project</button>
      </form>
    </div>
  );
}

export default ProjectPicker;
