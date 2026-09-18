import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Job } from "./types";

const TERMINAL: ReadonlySet<Job["status"]> = new Set(["succeeded", "failed", "cancelled"]);

export const isActive = (job: Job) => !TERMINAL.has(job.status);

/** Poll fast while something is running, slowly when nothing is. */
const ACTIVE_POLL_MS = 1000;
const IDLE_POLL_MS = 10000;

/**
 * The project's jobs, kept fresh.
 *
 * Polls rather than holding a stream open: the list view needs every
 * job anyway, and one cheap request a second is simpler to reason about
 * than a socket per job. The server merges each running job's live
 * progress into its row on the way out, so polling the list is enough
 * to drive a progress bar. (A per-job SSE endpoint exists for the hours
 * long training runs of a later phase, where polling would be wrong.)
 */
export function useJobs(projectId: string | null) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState<string | null>(null);
  // Held in a ref so the polling effect does not re-subscribe on every
  // tick, which would reset the interval and stall the bar.
  const hasActive = useRef(false);

  const refresh = useCallback(async () => {
    if (!projectId) {
      setJobs([]);
      return;
    }
    try {
      const next = await api.listJobs({ projectId });
      setJobs(next);
      hasActive.current = next.some(isActive);
      setError(null);
    } catch (e) {
      // A failed poll is not worth surfacing as a broken panel; the next
      // one usually succeeds. Keep the last known list on screen.
      setError(String(e));
    }
  }, [projectId]);

  useEffect(() => {
    if (!projectId) {
      setJobs([]);
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    const tick = async () => {
      await refresh();
      if (cancelled) return;
      timer = setTimeout(tick, hasActive.current ? ACTIVE_POLL_MS : IDLE_POLL_MS);
    };
    tick();

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [projectId, refresh]);

  const cancel = useCallback(
    async (job: Job) => {
      try {
        await api.cancelJob(job.id);
      } catch (e) {
        setError(String(e));
      } finally {
        // Refresh either way: on success to show the new state, on
        // failure because the job may well have finished on its own,
        // which is the likeliest reason a cancel did not land.
        refresh();
      }
    },
    [refresh],
  );

  return { jobs, activeJobs: jobs.filter(isActive), error, refresh, cancel };
}
