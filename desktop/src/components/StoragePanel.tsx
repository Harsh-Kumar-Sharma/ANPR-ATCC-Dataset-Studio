import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { IconAlert, IconChart } from "../Icons";
import type { Project, StorageUsage } from "../types";

interface Props {
  project: Project;
  /** Bumped when something is deleted elsewhere, so the numbers catch up. */
  refreshKey?: number;
}

/** Bytes as something a person can weigh a decision against. */
export function readableSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "unknown";
  if (bytes < 1024) return `${bytes} B`;

  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  // Rounded before the unit is settled: one byte short of a megabyte
  // is 1023.999 KB, which would otherwise print as "1024 KB".
  let shown = Number(value.toFixed(value < 10 ? 1 : 0));
  if (shown >= 1024 && unit < units.length - 1) {
    shown = 1;
    unit += 1;
  }
  return `${shown.toFixed(shown < 10 ? 1 : 0)} ${units[unit]}`;
}

/**
 * Where the disk went, and how to get some of it back.
 *
 * Every action here is safe in the same way: it removes either pixels
 * that can be regenerated - a decoded frame is a cache of the source
 * video - or an export the user names outright. None of it can touch a
 * label, a box or a frame row, which is what makes it usable in the
 * middle of a long run rather than only between them.
 */
function StoragePanel({ project, refreshKey = 0 }: Props) {
  const [usage, setUsage] = useState<StorageUsage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  const load = useCallback(async () => {
    try {
      setUsage(await api.getStorageUsage());
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  async function reclaim(what: () => Promise<{ reclaimed_bytes: number; detail: string }>) {
    setWorking(true);
    setError(null);
    try {
      const done = await what();
      setNotice(`${done.detail} Freed ${readableSize(done.reclaimed_bytes)}.`);
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setWorking(false);
    }
  }

  const mine = usage?.projects.find((p) => p.project_id === project.id);
  const orphanBytes = (usage?.orphan_workspaces ?? []).reduce((sum, o) => sum + o.bytes, 0);

  return (
    <div className="storage-panel">
      <div className="section-title">
        <IconChart /> Storage
      </div>

      {error && (
        <p className="error">
          <IconAlert /> {error}
        </p>
      )}
      {notice && <p className="status">{notice}</p>}

      {usage && (
        <>
          <p className="storage-free" data-testid="storage-free">
            {readableSize(usage.free_bytes)} free of {readableSize(usage.total_bytes)} on the drive. This app is
            holding {readableSize(usage.app_bytes)}.
          </p>

          {mine && (
            <ul className="storage-breakdown" data-testid="storage-breakdown">
              <li>
                <span>Source videos</span>
                <strong>{readableSize(mine.source_videos_bytes)}</strong>
              </li>
              <li>
                <span>Track crops</span>
                <strong>{readableSize(mine.track_crops_bytes)}</strong>
              </li>
              <li>
                <span>Decoded frames</span>
                <strong>{readableSize(mine.frame_images_bytes)}</strong>
                {/* The cheapest and safest thing to reclaim: these come
                    straight back out of the source video when a frame
                    is next opened. */}
                <button
                  disabled={working || mine.frame_images_bytes === 0}
                  onClick={() => reclaim(() => api.clearFrameImages(project.id))}
                >
                  Clear
                </button>
              </li>
              <li>
                <span>Exported datasets</span>
                <strong>{readableSize(mine.exports_bytes)}</strong>
              </li>
            </ul>
          )}

          <ul className="storage-breakdown">
            <li>
              <span>Job logs and progress files</span>
              <strong>{readableSize(usage.job_files_bytes)}</strong>
              <button disabled={working || usage.job_files_bytes === 0} onClick={() => reclaim(api.clearJobFiles)}>
                Clear
              </button>
            </li>
            <li>
              <span>Abandoned workspaces ({usage.orphan_workspaces.length})</span>
              <strong>{readableSize(orphanBytes)}</strong>
              <button
                disabled={working || usage.orphan_workspaces.length === 0}
                onClick={() => reclaim(api.removeOrphanWorkspaces)}
              >
                Remove
              </button>
            </li>
          </ul>

          <p className="storage-note">
            Clearing decoded frames and job files never removes a label or a frame - the images are decoded again the
            next time you open them. Deleting a source video or a dataset version does lose something, so those live
            where you can see what you are deleting.
          </p>
        </>
      )}
    </div>
  );
}

export default StoragePanel;
