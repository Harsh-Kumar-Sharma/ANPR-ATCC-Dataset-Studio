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
  // The remap prompt: which class is being deleted, how many labels it
  // holds, and where the user has chosen to send them.
  const [pendingDelete, setPendingDelete] = useState<{ cls: ClassDefinition; labelCount: number } | null>(null);
  const [remapTarget, setRemapTarget] = useState<number | null>(null);

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
      // Ask what is at stake before doing anything. A class nothing uses
      // just goes; one that labels point at needs the user to say where
      // those labels go, and that is a real decision, not a confirm box.
      const usage = await api.getClassUsage(project.id, cls.class_id);
      if (usage.label_count === 0) {
        await api.deleteClass(project.id, cls.class_id);
        setError(null);
        await refresh();
        onClassesChanged?.();
        return;
      }
      const others = classes.filter((c) => c.class_id !== cls.class_id);
      setPendingDelete({ cls, labelCount: usage.label_count });
      setRemapTarget(others[0]?.class_id ?? null);
      setError(null);
    } catch (e) {
      setError(describe(e));
    } finally {
      setBusy(false);
    }
  }

  async function settleDelete(options: { remapTo?: number; deleteLabels?: boolean }) {
    if (!pendingDelete) return;
    setBusy(true);
    try {
      await api.deleteClass(project.id, pendingDelete.cls.class_id, options);
      setPendingDelete(null);
      setError(null);
      await refresh();
      onClassesChanged?.();
    } catch (e) {
      setError(describe(e));
    } finally {
      setBusy(false);
    }
  }

  const remapChoices = pendingDelete ? classes.filter((c) => c.class_id !== pendingDelete.cls.class_id) : [];

  return (
    <section className="class-editor">
      <h3>Classes</h3>

      {error && (
        <p className="class-editor__error" role="alert">
          <IconAlert /> {error}
        </p>
      )}

      {pendingDelete && (
        <div className="class-editor__prompt" role="dialog" aria-label={`Delete ${pendingDelete.cls.name}`}>
          <p>
            {pendingDelete.labelCount} label(s) use <strong>{pendingDelete.cls.name}</strong>. Move them where, or
            delete them?
          </p>
          {remapChoices.length > 0 ? (
            <div className="class-editor__prompt-row">
              <label htmlFor="remap-target">Move to</label>
              <select
                id="remap-target"
                value={remapTarget ?? ""}
                onChange={(e) => setRemapTarget(Number(e.target.value))}
              >
                {remapChoices.map((c) => (
                  <option key={c.id} value={c.class_id}>
                    {c.name}
                  </option>
                ))}
              </select>
              <button
                disabled={busy || remapTarget === null}
                onClick={() => remapTarget !== null && settleDelete({ remapTo: remapTarget })}
              >
                Move
              </button>
            </div>
          ) : (
            <p className="class-editor__prompt-note">There is no other class to move them to.</p>
          )}
          <div className="class-editor__prompt-row">
            <button className="class-editor__danger" disabled={busy} onClick={() => settleDelete({ deleteLabels: true })}>
              Delete the labels too
            </button>
            <button disabled={busy} onClick={() => setPendingDelete(null)}>
              Keep the class
            </button>
          </div>
        </div>
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
