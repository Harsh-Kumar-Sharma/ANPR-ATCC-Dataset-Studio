import { useEffect, type ReactNode } from "react";
import { IconX } from "../Icons";

interface Props {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}

/**
 * A dialog over the app.
 *
 * Key presses inside it are kept from reaching the window: the label
 * canvas and the track review listen there for single-letter shortcuts,
 * and Delete on a focused button here must not delete a box behind it.
 */
function Modal({ title, onClose, children, wide = false }: Props) {
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        className={`modal${wide ? " modal--wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onKeyDown={(e) => {
          if (e.key === "Escape") onClose();
          e.stopPropagation();
        }}
      >
        <div className="modal__head">
          <h2>{title}</h2>
          <button className="btn-ghost modal__close" aria-label="Close" onClick={onClose}>
            <IconX />
          </button>
        </div>
        <div className="modal__body">{children}</div>
      </div>
    </div>
  );
}

export default Modal;
