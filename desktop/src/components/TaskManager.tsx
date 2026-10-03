import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { IconAlert, IconCheck, IconPlus } from "../Icons";
import { sourceLabel } from "../sourceLabel";
import type { LabelingTask, Project, SourceQueue, TaskStatus, User } from "../types";
import { TASK_STATUS_LABEL, TaskProgressBar, TaskStatusBadge, taskSourceName } from "./taskBits";

interface Props {
  project: Project;
  /** Open a task's frames to look at or label them. */
  onOpen: (task: LabelingTask) => void;
}

type Filter = "all" | TaskStatus;
const FILTERS: Filter[] = ["all", "assigned", "in_progress", "in_review", "done"];

function describe(e: unknown): string {
  return e instanceof ApiError ? e.message : String(e);
}

/**
 * Handing out a project's labelling, and reviewing it. Admins only.
 *
 * One source is one task: picking a source and a person is all it
 * takes. Submitted tasks wait here for Accept, or Reject with a reason.
 */
function TaskManager({ project, onOpen }: Props) {
  const [tasks, setTasks] = useState<LabelingTask[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [sources, setSources] = useState<SourceQueue[]>([]);
  const [filter, setFilter] = useState<Filter>("all");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const [newSource, setNewSource] = useState("");
  const [newAssignee, setNewAssignee] = useState("");
  const [rejecting, setRejecting] = useState<{ task: LabelingTask; note: string } | null>(null);
  const [doomed, setDoomed] = useState<LabelingTask | null>(null);

  const load = useCallback(async () => {
    try {
      const [t, u, s] = await Promise.all([
        api.listTasks(project.id),
        api.listUsers(),
        api.getQueueBySource(project.id),
      ]);
      setTasks(t);
      setUsers(u);
      setSources(s);
      setError(null);
    } catch (e) {
      setError(describe(e));
    }
  }, [project.id]);

  useEffect(() => {
    load();
  }, [load]);

  async function act(key: string, what: () => Promise<unknown>, said: string) {
    setBusy(key);
    setError(null);
    setNotice(null);
    try {
      await what();
      setNotice(said);
      await load();
    } catch (e) {
      setError(describe(e));
    } finally {
      setBusy(null);
    }
  }

  const tasked = new Set(tasks.map((t) => t.source_id));
  const free = sources.filter((s) => !tasked.has(s.source_id));
  const people = users.filter((u) => u.is_active);
  const freeNames = free.map((s) => sourceLabel(s));
  const sharedNames = new Set(freeNames).size < freeNames.length;
  const shown = filter === "all" ? tasks : tasks.filter((t) => t.status === filter);
  const count = (f: Filter) => (f === "all" ? tasks.length : tasks.filter((t) => t.status === f).length);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    const source = free.find((s) => s.source_id === newSource);
    const person = people.find((u) => u.id === newAssignee);
    if (!source || !person) return;
    await act(
      "new",
      () => api.createTask(project.id, source.source_id, person.id),
      `${sourceLabel(source)} is now ${person.display_name}'s task.`,
    );
    setNewSource("");
  }

  return (
    <div className="task-manager">
      <form className="task-new" onSubmit={create}>
        <div className="task-new__title">
          <IconPlus /> New task
        </div>
        <label className="auth-field">
          Source
          <select aria-label="Source to assign" value={newSource} onChange={(e) => setNewSource(e.target.value)}>
            <option value="">{free.length ? "Choose a source…" : "Every source already has a task"}</option>
            {free.map((s) => (
              <option key={s.source_id} value={s.source_id}>
                {sourceLabel(s)} · {s.total} frames, {s.pending} pending
              </option>
            ))}
          </select>
        </label>
        <label className="auth-field">
          Assign to
          <select aria-label="Assign to" value={newAssignee} onChange={(e) => setNewAssignee(e.target.value)}>
            <option value="">Choose a person…</option>
            {people.map((u) => (
              <option key={u.id} value={u.id}>
                {u.display_name} (@{u.username}){u.role === "admin" ? " · admin" : ""}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" className="btn-primary" disabled={!newSource || !newAssignee || busy === "new"}>
          {busy === "new" ? "Assigning…" : "Assign"}
        </button>
        {sharedNames && (
          <p className="task-new__hint">
            Some sources share a name. Rename them in Sources (the pencil beside each name) to tell them apart.
          </p>
        )}
      </form>

      {error && (
        <p className="error" role="alert">
          <IconAlert /> {error}
        </p>
      )}
      {notice && (
        <p className="status" role="status">
          <IconCheck /> {notice}
        </p>
      )}

      <div className="task-filters" role="tablist" aria-label="Filter tasks">
        {FILTERS.map((f) => (
          <button
            key={f}
            role="tab"
            aria-selected={filter === f}
            className={filter === f ? "selected" : ""}
            onClick={() => setFilter(f)}
          >
            {f === "all" ? "All" : TASK_STATUS_LABEL[f]} <span className="task-filters__n">{count(f)}</span>
          </button>
        ))}
      </div>

      {rejecting && (
        <div className="users-confirm" role="alert">
          <p className="delete-confirm-title">
            Send <strong>{taskSourceName(rejecting.task)}</strong> back to{" "}
            {rejecting.task.assignee?.display_name ?? "its assignee"}?
          </p>
          <label className="auth-field">
            What needs fixing
            <input
              type="text"
              autoFocus
              value={rejecting.note}
              placeholder="e.g. plates on frames 120-140 are missing"
              onChange={(e) => setRejecting({ ...rejecting, note: e.target.value })}
            />
          </label>
          <div className="delete-confirm-actions">
            <button onClick={() => setRejecting(null)}>Cancel</button>
            <button
              className="btn-danger"
              disabled={!rejecting.note.trim() || busy !== null}
              onClick={async () => {
                const { task, note } = rejecting;
                await act(task.id, () => api.rejectTask(task.id, note), `Sent ${taskSourceName(task)} back.`);
                setRejecting(null);
              }}
            >
              Send back
            </button>
          </div>
        </div>
      )}

      {doomed && (
        <div className="delete-confirm" role="alert">
          <p className="delete-confirm-title">
            Delete the task for <strong>{taskSourceName(doomed)}</strong>? Its frames and labels stay; the source just
            stops being anyone's task.
          </p>
          <div className="delete-confirm-actions">
            <button onClick={() => setDoomed(null)}>Cancel</button>
            <button
              className="btn-danger"
              disabled={busy !== null}
              onClick={async () => {
                const task = doomed;
                await act(task.id, () => api.deleteTask(task.id), `Deleted the task for ${taskSourceName(task)}.`);
                setDoomed(null);
              }}
            >
              Delete task
            </button>
          </div>
        </div>
      )}

      {tasks.length === 0 ? (
        <p className="empty">No tasks yet. Pick a source and a person above to hand out the first one.</p>
      ) : (
        <div className="users-table-wrap">
          <table className="users-table task-table">
            <thead>
              <tr>
                <th>Source</th>
                <th>Assigned to</th>
                <th>Progress</th>
                <th>Status</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {shown.map((task) => {
                const working = busy === task.id;
                const name = taskSourceName(task);
                return (
                  <tr key={task.id}>
                    <td>
                      <button className="task-table__open" onClick={() => onOpen(task)} title="Open its frames">
                        {name}
                      </button>
                      {task.review_note && task.status === "in_progress" && (
                        <span className="task-table__note">Sent back: {task.review_note}</span>
                      )}
                    </td>
                    <td>
                      <select
                        aria-label={`Assignee of ${name}`}
                        value={task.assignee?.id ?? ""}
                        disabled={working}
                        onChange={(e) => {
                          const person = people.find((u) => u.id === e.target.value);
                          if (person)
                            act(task.id, () => api.reassignTask(task.id, person.id), `${name} is now ${person.display_name}'s.`);
                        }}
                      >
                        {!task.assignee && <option value="">Nobody - pick someone</option>}
                        {people.map((u) => (
                          <option key={u.id} value={u.id}>
                            {u.display_name}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="task-table__progress">
                      <TaskProgressBar task={task} />
                    </td>
                    <td>
                      <TaskStatusBadge status={task.status} />
                    </td>
                    <td>
                      <span className="users-table__actions">
                        <button onClick={() => onOpen(task)}>Open</button>
                        {task.status === "in_review" && (
                          <>
                            <button
                              className="btn-primary"
                              disabled={working}
                              onClick={() => act(task.id, () => api.acceptTask(task.id), `${name} accepted - done.`)}
                            >
                              Accept
                            </button>
                            <button
                              className="btn-danger-ghost"
                              disabled={working}
                              onClick={() => setRejecting({ task, note: "" })}
                            >
                              Reject
                            </button>
                          </>
                        )}
                        {task.status === "done" && (
                          <button
                            disabled={working}
                            onClick={() => act(task.id, () => api.reopenTask(task.id), `${name} reopened.`)}
                          >
                            Reopen
                          </button>
                        )}
                        <button
                          className="btn-danger-ghost"
                          aria-label={`Delete task ${name}`}
                          disabled={working}
                          onClick={() => setDoomed(task)}
                        >
                          Delete
                        </button>
                      </span>
                    </td>
                  </tr>
                );
              })}
              {shown.length === 0 && (
                <tr>
                  <td colSpan={5} className="task-table__none">
                    No tasks {filter === "all" ? "" : `are ${TASK_STATUS_LABEL[filter as TaskStatus].toLowerCase()}`}.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default TaskManager;
