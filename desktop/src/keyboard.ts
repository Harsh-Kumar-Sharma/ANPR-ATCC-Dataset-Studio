/**
 * Is a key event aimed at something that already consumes typing?
 *
 * Single-letter shortcuts live on the window so they work without the
 * panel being focused. That only stays safe if typing into a field
 * never triggers them - "s" in a plate-text box must not save the frame.
 */
export function isEditableTarget(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLInputElement ||
    target instanceof HTMLSelectElement ||
    target instanceof HTMLTextAreaElement ||
    (target instanceof HTMLElement && target.isContentEditable)
  );
}
