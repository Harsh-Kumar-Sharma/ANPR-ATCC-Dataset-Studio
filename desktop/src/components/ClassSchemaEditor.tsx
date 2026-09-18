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

  /** Surface the backend's own message. A name clash, or deleting a
   *  class that is still in use, are ordinary things to do by accident,
   *  and the server already says exactly which name or how many labels. */
  function describe(e: unknown): string {
    return e instanceof ApiError ? e.message : String(e);
  }

  const refresh = useCallback(async () => {
    try {
      setClasses(await api.listClasses(project.id));
      setError(null);
    } catch (e) {
      setError(describe(e));
    }
  }, [project.id]);

  useEffect(() => {
    refresh();
  }, [refresh]);

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
      // No pre-check: the server refuses a class that is in use and its
      // message already carries the count, so asking first would only
      // duplicate that wording in a second place - and race it.
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
