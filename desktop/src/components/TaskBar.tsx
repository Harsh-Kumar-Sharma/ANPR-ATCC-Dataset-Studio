import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { IconAlert } from "../Icons";
import type { LabelingTask } from "../types";
import { TaskProgressBar, TaskStatusBadge, canSubmit } from "./taskBits";

interface Props {
  task: LabelingTask;
  /** Bumped when a frame is saved or skipped, so the counts catch up. */
  refreshKey: number;
  onChanged: (task: LabelingTask) => void;
}

/**
 * The open task, above its frames: how far along it is, and handing it
 * in - or, for an admin reviewing it, accepting or sending it back.
 */
function TaskBar({ task, refreshKey, onChanged }: Props) {
  const { user } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [rejectNote, setRejectNote] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getTask(task.id)
      .then((fresh) => !cancelled && onChanged(fresh))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
    // onChanged is a setter from above; the fetch follows the work.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task.id, refreshKey]);

  async function run(what: () => Promise<LabelingTask>) {
    setBusy(true);
    setError(null);
    try {
      onChanged(await what());
      setRejectNote(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const mine = task.assignee?.id === user?.id;
  const admin = user?.role === "admin";

  return (
    <div className="task-bar">
      <div className="task-bar__row">
        <TaskStatusBadge status={task.status} />
        <TaskProgressBar task={task} />
        {task.assignee && !mine && <span className="task-bar__who">Assigned to {task.assignee.display_name}</span>}
        <span className="task-bar__actions">
          {mine && (task.status === "assigned" || task.status === "in_progress") && (
            <button
              className="btn-primary"
              disabled={!canSubmit(task) || busy}
              title={canSubmit(task) ? "Hand it in for review" : "Label or skip every frame first"}
              onClick={() => run(() => api.submitTask(task.id))}
            >
              Submit for review
            </button>
          )}
          {admin && task.status === "in_review" && (
            <>
              <button className="btn-primary" disabled={busy} onClick={() => run(() => api.acceptTask(task.id))}>
                Accept
              </button>
              <button className="btn-danger-ghost" disabled={busy} onClick={() => setRejectNote("")}>
                Reject
              </button>
            </>
          )}
          {admin && task.status === "done" && (
            <button disabled={busy} onClick={() => run(() => api.reopenTask(task.id))}>
              Reopen
            </button>
          )}
        </span>
      </div>

      {task.review_note && task.status === "in_progress" && (
        <p className="warning">
          <IconAlert /> Sent back: {task.review_note}
        </p>
      )}

      {rejectNote !== null && (
        <div className="task-bar__reject">
          <input
            type="text"
            autoFocus
            aria-label="What needs fixing"
            placeholder="What needs fixing?"
            value={rejectNote}
            onChange={(e) => setRejectNote(e.target.value)}
          />
          <button
            className="btn-danger"
            disabled={!rejectNote.trim() || busy}
            onClick={() => run(() => api.rejectTask(task.id, rejectNote))}
          >
            Send back
          </button>
          <button onClick={() => setRejectNote(null)}>Cancel</button>
        </div>
      )}

      {error && (
        <p className="error" role="alert">
          <IconAlert /> {error}
        </p>
      )}
    </div>
  );
}

export default TaskBar;
