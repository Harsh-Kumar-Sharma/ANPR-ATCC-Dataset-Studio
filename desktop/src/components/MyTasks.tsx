import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { IconAlert, IconCheck, IconFilm } from "../Icons";
import type { LabelingTask, Project } from "../types";
import { TaskProgressBar, TaskStatusBadge, canSubmit, taskSourceName } from "./taskBits";

interface Props {
  project: Project;
  onOpen: (task: LabelingTask) => void;
}

function describe(e: unknown): string {
  return e instanceof ApiError ? e.message : String(e);
}

/** The work given to you in this project, and handing it in. */
function MyTasks({ project, onOpen }: Props) {
  const [tasks, setTasks] = useState<LabelingTask[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setTasks(await api.listTasks(project.id));
      setError(null);
    } catch (e) {
      setError(describe(e));
    }
  }, [project.id]);

  useEffect(() => {
    load();
  }, [load]);

  async function submit(task: LabelingTask) {
    setBusy(task.id);
    setError(null);
    try {
      await api.submitTask(task.id);
      setNotice(`${taskSourceName(task)} submitted for review.`);
      await load();
    } catch (e) {
      setError(describe(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="my-tasks">
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

      {tasks !== null && tasks.length === 0 && (
        <p className="empty">Nothing has been given to you in this project yet.</p>
      )}

      <div className="task-cards">
        {(tasks ?? []).map((task) => (
          <article key={task.id} className="card task-card">
            <div className="task-card__head">
              <span className="task-card__icon" aria-hidden="true">
                <IconFilm />
              </span>
              <h3 className="task-card__name" title={task.source_path_or_uri}>
                {taskSourceName(task)}
              </h3>
              <TaskStatusBadge status={task.status} />
            </div>

            <TaskProgressBar task={task} />

            {task.review_note && task.status === "in_progress" && (
              <p className="warning">
                <IconAlert /> Sent back: {task.review_note}
              </p>
            )}
            {task.status === "in_review" && <p className="task-card__hint">Waiting for an admin to review it.</p>}
            {task.status === "done" && <p className="task-card__hint">Accepted. Nothing more to do.</p>}

            <div className="task-card__actions">
              <button className={task.status === "assigned" ? "btn-primary" : ""} onClick={() => onOpen(task)}>
                {task.status === "assigned" ? "Start" : task.status === "in_progress" ? "Continue" : "Open"}
              </button>
              {(task.status === "assigned" || task.status === "in_progress") && (
                <button
                  className={canSubmit(task) ? "btn-primary" : ""}
                  disabled={!canSubmit(task) || busy === task.id}
                  title={canSubmit(task) ? "Hand it in for review" : "Label or skip every frame first"}
                  onClick={() => submit(task)}
                >
                  {busy === task.id ? "Submitting…" : "Submit for review"}
                </button>
              )}
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}

export default MyTasks;
