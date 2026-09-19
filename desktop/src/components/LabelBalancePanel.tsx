import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { IconAlert, IconChart } from "../Icons";
import type { LabelBalance, Project } from "../types";

interface Props {
  project: Project;
  /** Bumped when labelling changes, so the balance catches up. */
  refreshKey?: number;
}

/**
 * What this project has actually been labelled with.
 *
 * Deliberately not part of the evaluation report. That one is scoped to
 * a processing run, which is right for asking how a detection pass went
 * and useless for asking what a person has produced: a box drawn on the
 * labelling canvas belongs to a frame, and the frame's source may have
 * several runs. Someone labelling entirely in the canvas could not ask
 * the question at all, and the empty answer they got read as "you have
 * nothing" rather than "this cannot tell you".
 */
function LabelBalancePanel({ project, refreshKey = 0 }: Props) {
  const [balance, setBalance] = useState<LabelBalance | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getLabelBalance(project.id)
      .then((next) => {
        if (cancelled) return;
        setBalance(next);
        setError(null);
      })
      .catch((e) => !cancelled && setError(e instanceof ApiError ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
  }, [project.id, refreshKey]);

  // From the data rather than from the first row: reading the widest
  // bar off the server's sort order couples the drawing to it silently.
  const most = Math.max(0, ...(balance?.classes ?? []).map((c) => c.box_count));

  return (
    <div className="label-balance-panel">
      <div className="section-title">
        <IconChart /> Your labels
      </div>

      {error && (
        <p className="error">
          <IconAlert /> {error}
        </p>
      )}

      {balance && (
        <>
          <p className="label-balance-summary" data-testid="label-balance-summary">
            {balance.total_boxes} box(es) across {balance.labeled_frames} labelled frame(s)
            {balance.background_frames > 0 && `, ${balance.background_frames} of them labelled empty`}.
          </p>

          {balance.unclassified_boxes > 0 && (
            // These do not export. Without saying so, "my dataset is
            // smaller than my labelling" has no explanation anywhere.
            <p className="warning">
              <IconAlert /> {balance.unclassified_boxes} box(es) still need a class before they can be exported.
            </p>
          )}

          <ul className="class-distribution-list">
            {balance.classes.map((row) => (
              <li key={row.class_id}>
                <span>{row.name}</span>
                {/* A bar rather than only a number: the thing worth
                    seeing here is the imbalance, and forty-two next to
                    seven does not show it the way two bars do. */}
                <span
                  className="class-balance-bar"
                  style={{ width: `${most > 0 ? Math.max(4, (row.box_count / most) * 100) : 0}%` }}
                  aria-hidden="true"
                />
                <strong>{row.box_count}</strong>
              </li>
            ))}
            {balance.classes.length === 0 && <li className="empty">No labels yet - open the Label tab to start.</li>}
          </ul>
        </>
      )}
    </div>
  );
}

export default LabelBalancePanel;
