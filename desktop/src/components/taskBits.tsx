import { sourceLabel } from "../sourceLabel";
import type { LabelingTask, TaskStatus } from "../types";

export const TASK_STATUS_LABEL: Record<TaskStatus, string> = {
  assigned: "Assigned",
  in_progress: "In progress",
  in_review: "In review",
  done: "Done",
};

const TASK_STATUS_BADGE: Record<TaskStatus, string> = {
  assigned: "badge-neutral",
  in_progress: "badge-accent",
  in_review: "badge-warning",
  done: "badge-success",
};

export function TaskStatusBadge({ status }: { status: TaskStatus }) {
  return <span className={`badge ${TASK_STATUS_BADGE[status]}`}>{TASK_STATUS_LABEL[status]}</span>;
}

export function taskSourceName(task: LabelingTask): string {
  return sourceLabel({ type: task.source_type, path_or_uri: task.source_path_or_uri });
}

/** Frames done (labelled or set aside) out of all of them. */
export function TaskProgressBar({ task }: { task: LabelingTask }) {
  const { total, pending, labeled } = task.progress;
  const done = total - pending;
  const percent = total > 0 ? Math.round((done / total) * 100) : 0;
  return (
    <div className="task-progress">
      <div
        className="task-progress__bar"
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`${taskSourceName(task)} progress`}
      >
        <div className="task-progress__fill" style={{ width: `${percent}%` }} />
      </div>
      <span className="task-progress__text">
        {done}/{total} done · {labeled} labelled · {pending} left
      </span>
    </div>
  );
}

/** Ready to hand in: begun or not, with nothing left pending. */
export function canSubmit(task: LabelingTask): boolean {
  return (task.status === "assigned" || task.status === "in_progress") && task.progress.pending === 0;
}
