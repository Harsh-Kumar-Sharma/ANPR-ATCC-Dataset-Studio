import type { Job } from "../types";
import { isActive } from "../useJobs";
import ProgressBar from "./ProgressBar";

interface Props {
  jobs: Job[];
  error?: string | null;
  onCancel?: (job: Job) => void;
}

const STATUS_LABEL: Record<Job["status"], string> = {
  pending: "Queued",
  running: "Running",
  succeeded: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
};

const TYPE_LABEL: Record<Job["type"], string> = {
  detect: "Detect + track",
  train: "Training",
  export: "Export",
  preannotate: "Pre-annotation",
};

function formatWhen(job: Job): string {
  const stamp = job.completed_at ?? job.started_at ?? job.created_at;
  const date = new Date(stamp);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString();
}

function JobRow({ job, onCancel }: { job: Job; onCancel?: (job: Job) => void }) {
  return (
    <li className={`job-row job-row--${job.status}`} data-testid="job-row">
      <div className="job-row__head">
        <span className="job-row__type">{TYPE_LABEL[job.type] ?? job.type}</span>
        <span className="job-row__status">{STATUS_LABEL[job.status] ?? job.status}</span>
        <span className="job-row__when">{formatWhen(job)}</span>
        {isActive(job) && onCancel && (
          <button className="job-row__cancel" onClick={() => onCancel(job)}>
            Cancel
          </button>
        )}
      </div>

      {isActive(job) && (
        <ProgressBar
          fraction={job.progress}
          label={`${TYPE_LABEL[job.type] ?? job.type} progress`}
          className="job-row__bar"
        />
      )}

      {isActive(job) && <p className="job-row__message">{job.progress_message ?? "Starting..."}</p>}

      {/* The error is the whole reason a failed job is still listed - it
          used to vanish into a 500 with no trace. */}
      {job.status === "failed" && job.error_message && <p className="job-row__error">{job.error_message}</p>}
    </li>
  );
}

function JobsPanel({ jobs, error, onCancel }: Props) {
  return (
    <section className="jobs-panel">
      <h3>Jobs</h3>
      {error && <p className="jobs-panel__error">{error}</p>}
      {jobs.length === 0 ? (
        <p className="jobs-panel__empty">Nothing has run yet.</p>
      ) : (
        <ul className="jobs-panel__list">
          {jobs.map((job) => (
            <JobRow key={job.id} job={job} onCancel={onCancel} />
          ))}
        </ul>
      )}
    </section>
  );
}

export default JobsPanel;
