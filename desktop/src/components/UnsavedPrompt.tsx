import type { Frame } from "../types";

interface Props {
  frame: Frame;
  onResolve: (discard: boolean) => void;
  className?: string;
}

/**
 * The question asked when a move would abandon unsaved boxes.
 *
 * Shared because the move can be asked for in two places now - the
 * queue list and the buttons under the canvas - and the question has
 * to appear where the user is looking.
 */
function UnsavedPrompt({ frame, onResolve, className = "label-queue__prompt" }: Props) {
  return (
    <div className={className} role="alert">
      <p>This frame has unsaved boxes.</p>
      <div className="label-queue__prompt-row">
        <button onClick={() => onResolve(true)}>Discard and open frame {frame.frame_index}</button>
        <button onClick={() => onResolve(false)}>Stay here</button>
      </div>
    </div>
  );
}

export default UnsavedPrompt;
