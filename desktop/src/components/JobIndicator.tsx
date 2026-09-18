import type { Job } from "../types";
import ProgressBar from "./ProgressBar";

interface Props {
  activeJobs: Job[];
}

/**
 * The always-visible "something is running" indicator.
 *
 * Deliberately global rather than living in the sources panel: the
 * whole point of moving processing into a job is that you can walk away
 * from the screen that started it, and you still need to know it is
 * going.
 */
function JobIndicator({ activeJobs }: Props) {
  if (activeJobs.length === 0) return null;

  const [job, ...rest] = activeJobs;
  const percent = Math.round(job.progress * 100);

  return (
    <div className="job-indicator" data-testid="job-indicator">
      <span className="job-indicator__spinner" aria-hidden="true" />
      <span className="job-indicator__text">
        {job.progress_message ?? "Working"}
        {rest.length > 0 && ` (+${rest.length} more)`}
      </span>
      <ProgressBar fraction={job.progress} label="Background job progress" className="job-indicator__bar" />
      <span className="job-indicator__percent">{percent}%</span>
    </div>
  );
}

export default JobIndicator;
