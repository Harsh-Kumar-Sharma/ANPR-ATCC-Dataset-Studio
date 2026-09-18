import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { IconAlert } from "../Icons";
import type { ClassDefinition, Project } from "../types";

interface Props {
  project: Project;
  /** Called after any change, so whatever else is showing classes can
   *  catch up - a rename has to reach the review panel. */
  onClassesChanged?: () => void;
}

function ClassSchemaEditor({ project, onClassesChanged }: Props) {
  const [classes, setClasses] = useState<ClassDefinition[]>([]);
  const [newName, setNewName] = useState("");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [draftName, setDraftName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setClasses(await api.listClasses(project.id));
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [project.id]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  /** Surface the backend's own message. A name clash is a normal thing to
   *  do by accident, and "this project already has a class called
   *  'vehicle'" is far more use than a generic failure. */
  function describe(e: unknown): string {
    return e instanceof ApiError ? e.message : String(e);
  }

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    if (!newName.trim()) return;
    setBusy(true);
    try {
      await api.createClass(project.id, newName.trim());
      setNewName("");
      setError(null);
      await refresh();
      onClassesChanged?.();
    } catch (e) {
      setError(describe(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleRename(classId: number) {
    const name = draftName.trim();
    if (!name) return;
    setBusy(true);
    try {
      await api.renameClass(project.id, classId, name);
      setEditingId(null);
      setError(null);
      await refresh();
      onClassesChanged?.();
    } catch (e) {
      setError(describe(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(cls: ClassDefinition) {
    setBusy(true);
    try {
      // Ask first, so the refusal is explained before it happens rather
      // than after. Deleting labels, or moving them, is a decision the
      // user has to make - and that conversation does not exist yet.
      const usage = await api.getClassUsage(project.id, cls.class_id);
      if (usage.label_count > 0) {
        setError(
          `${usage.label_count} label(s) use "${cls.name}". Deleting a class that is in use is not supported yet.`,
        );
        return;
      }
      await api.deleteClass(project.id, cls.class_id);
      setError(null);
      await refresh();
      onClassesChanged?.();
    } catch (e) {
      setError(describe(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="class-editor">
      <h3>Classes</h3>

      {error && (
        <p className="class-editor__error" role="alert">
          <IconAlert /> {error}
        </p>
      )}

      {classes.length === 0 ? (
        <p className="class-editor__empty">No classes yet - add the first one below.</p>
      ) : (
        <ul className="class-editor__list">
          {classes.map((cls) => (
            <li key={cls.id} className="class-editor__row">
              {editingId === cls.class_id ? (
                <>
                  <input
                    aria-label={`New name for ${cls.name}`}
                    value={draftName}
                    autoFocus
                    onChange={(e) => setDraftName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleRename(cls.class_id);
                      if (e.key === "Escape") setEditingId(null);
                    }}
                  />
                  <button onClick={() => handleRename(cls.class_id)} disabled={busy}>
                    Save
                  </button>
                  <button onClick={() => setEditingId(null)}>Cancel</button>
                </>
              ) : (
                <>
                  <span className="class-editor__name">{cls.name}</span>
                  <button
                    aria-label={`Rename ${cls.name}`}
                    onClick={() => {
                      setEditingId(cls.class_id);
                      setDraftName(cls.name);
                    }}
                  >
                    Rename
                  </button>
                  <button aria-label={`Delete ${cls.name}`} onClick={() => handleDelete(cls)} disabled={busy}>
                    Delete
                  </button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}

      <form onSubmit={handleAdd} className="class-editor__add">
        <input
          aria-label="New class name"
          placeholder="New class name"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
        />
        <button type="submit" disabled={busy || !newName.trim()}>
          Add
        </button>
      </form>
    </section>
  );
}

export default ClassSchemaEditor;
