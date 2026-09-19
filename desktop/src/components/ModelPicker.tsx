import { useEffect, useState } from "react";
import { api } from "../api";
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

  useEffect(() => {
    let cancelled = false;
    api
      .listModels()
      .then((list) => {
        if (!cancelled) setModels(list);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    setChosen(readLastModel(projectId));
  }, [projectId]);

  const known = models.some((m) => m.id === chosen);
  const modelId = known ? chosen : (models[0]?.id ?? null);

  function choose(next: string) {
    setChosen(next);
    writeLastModel(projectId, next);
  }

  return { models, modelId, choose, error };
}

interface Props {
  choice: ModelChoice;
  label?: string;
  disabled?: boolean;
  /** Distinguishes the two pickers on screen for the accessible name. */
  id?: string;
}

/** The choice itself: what will detect, and what that means. */
function ModelPicker({ choice, label = "Model", disabled = false, id = "model" }: Props) {
  const { models, modelId } = choice;
  const selected = models.find((m) => m.id === modelId) ?? null;

  if (models.length === 0) return null;

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
    </div>
  );
}

export default ModelPicker;
