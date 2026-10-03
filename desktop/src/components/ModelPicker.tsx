import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { ModelInfo } from "../types";

/** The last model used, per project. A project is one kind of footage;
 *  the model that suited it last time is the right default this time. */
const lastModelKey = (projectId: string) => `anpr:last-model:${projectId}`;

function readLastModel(projectId: string): string | null {
  try {
    return window.localStorage.getItem(lastModelKey(projectId));
  } catch {
    return null;
  }
}

function writeLastModel(projectId: string, modelId: string): void {
  try {
    window.localStorage.setItem(lastModelKey(projectId), modelId);
  } catch {
    // Private mode, or storage full. The default just resets.
  }
}

export interface ModelChoice {
  models: ModelInfo[];
  /** The chosen model, or null before the list has arrived. */
  modelId: string | null;
  choose: (modelId: string) => void;
  error: string | null;
  /** Re-read the list, after importing or removing one. */
  reload: () => Promise<void>;
  /** The project these models are being chosen for. Carried here so
   *  an import is recorded against it rather than becoming another
   *  entry in every other project's menu. */
  projectId: string;
}

/**
 * Which model detects, remembered per project.
 *
 * A model that has since been removed from the models directory falls
 * back to the first one offered rather than sending an id the backend
 * will refuse - the user did not choose that outcome, and a 404 on
 * Detect is a confusing way to learn a file was deleted.
 */
export function useModelChoice(projectId: string): ModelChoice {
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [chosen, setChosen] = useState<string | null>(() => readLastModel(projectId));
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setModels(await api.listModels(projectId));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [projectId]);

  useEffect(() => {
    reload();
  }, [reload]);

  useEffect(() => {
    setChosen(readLastModel(projectId));
  }, [projectId]);

  const known = models.some((m) => m.id === chosen);
  const modelId = known ? chosen : (models[0]?.id ?? null);

  function choose(next: string) {
    setChosen(next);
    writeLastModel(projectId, next);
  }

  return { models, modelId, choose, error, reload, projectId };
}

interface Props {
  choice: ModelChoice;
  label?: string;
  disabled?: boolean;
  /** Distinguishes the two pickers on screen for the accessible name. */
  id?: string;
  /** Offer importing and removing a model here. On by default; the
   *  live-stream picker turns it off so the same controls do not
   *  appear twice in one sidebar. */
  manage?: boolean;
}

/** The choice itself: what will detect, and what that means. */
function ModelPicker({ choice, label = "Model", disabled = false, id = "model", manage = true }: Props) {
  const { models, modelId } = choice;
  const selected = models.find((m) => m.id === modelId) ?? null;
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [importing, setImporting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [said, setSaid] = useState<string | null>(null);

  async function doImport(e: React.FormEvent) {
    e.preventDefault();
    if (!path.trim()) return;
    setBusy(true);
    setProblem(null);
    setSaid(null);
    try {
      // Slow on purpose: the backend loads the checkpoint to check it
      // is one, which is seconds rather than instant.
      const added = await api.importModel(path.trim(), name, choice.projectId);
      await choice.reload();
      choice.choose(added.id);
      setPath("");
      setName("");
      setImporting(false);
      setSaid(`Added ${added.label}.`);
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function doRemove(model: ModelInfo) {
    setBusy(true);
    setProblem(null);
    setSaid(null);
    try {
      await api.deleteModel(model.id);
      await choice.reload();
      setSaid(`Removed ${model.label}.`);
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (models.length === 0 && !manage) return null;

  return (
    <div className="model-picker">
      <label className="model-picker__row" htmlFor={`${id}-select`}>
        <span>{label}</span>
        <select
          id={`${id}-select`}
          value={modelId ?? ""}
          disabled={disabled}
          onChange={(e) => choice.choose(e.target.value)}
        >
          {models.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label}
              {m.kind === "custom" ? " (yours)" : ""}
            </option>
          ))}
        </select>
      </label>
      {selected && <p className="model-picker__note">{selected.note}</p>}
      {/* Said before the run, not discovered during it: the first use
          of a model that is not on disk yet pauses to download it. */}
      {selected && !selected.present && (
        <p className="model-picker__note model-picker__note--fetch">
          Not downloaded yet - the first run will fetch it once.
        </p>
      )}
      {choice.error && <p className="error">{choice.error}</p>}

      {manage && (
        <div className="model-picker__manage">
          {/* Removing is offered only for the selected model, and only
              when it is yours: a built-in's weights would come back on
              the next run anyway. */}
          {selected?.present && (
            <a
              className="button model-picker__download"
              href={api.modelWeightsUrl(selected.id)}
              download={`${selected.id}.pt`}
              title="Save this model's .pt file - to use it in production or on another machine"
            >
              Download {selected.label} (.pt)
            </a>
          )}
          {selected?.kind === "custom" && (
            <button
              type="button"
              className="model-picker__remove"
              disabled={busy}
              onClick={() => doRemove(selected)}
            >
              Remove {selected.label}
            </button>
          )}

          {importing ? (
            <form className="model-picker__import" onSubmit={doImport}>
              <input
                type="text"
                aria-label="Path to a trained model (.pt)"
                placeholder="C:\runs\detect\train\weights\best.pt"
                value={path}
                onChange={(e) => setPath(e.target.value)}
              />
              <input
                type="text"
                aria-label="Name for this model"
                placeholder="Name it (optional)"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
              <div className="model-picker__import-row">
                <button type="submit" disabled={!path.trim() || busy}>
                  {busy ? "Checking the file…" : "Add model"}
                </button>
                <button type="button" disabled={busy} onClick={() => setImporting(false)}>
                  Cancel
                </button>
              </div>
              <p className="model-picker__note">
                The file is copied in and loaded once to check it is a YOLO checkpoint, so this takes a
                few seconds.
              </p>
            </form>
          ) : (
            <button type="button" className="model-picker__add" onClick={() => setImporting(true)}>
              + Use a model you trained
            </button>
          )}

          {problem && <p className="error">{problem}</p>}
          {said && <p className="status">{said}</p>}
        </div>
      )}
    </div>
  );
}

export default ModelPicker;
