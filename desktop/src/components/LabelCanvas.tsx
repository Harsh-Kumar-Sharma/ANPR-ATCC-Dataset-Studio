import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import { IconAlert } from "../Icons";
import type { Annotation, Frame, FrameAnnotationWrite } from "../types";

interface Props {
  frame: Frame;
  onSaved?: (frame: Frame) => void;
}

type Box = FrameAnnotationWrite;

/** A box being dragged out, in full-frame pixels. Not yet normalised: x2
 *  may be left of x1 until the mouse comes up. */
interface Draft {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

/** Boxes smaller than this in either dimension are accidental clicks,
 *  not labels. In full-frame pixels. */
const MIN_BOX_SIDE = 2;

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

function normalise(d: Draft): [number, number, number, number] {
  return [Math.min(d.x1, d.x2), Math.min(d.y1, d.y2), Math.max(d.x1, d.x2), Math.max(d.y1, d.y2)];
}

/** A loaded annotation, carried with its id so saving it back keeps it
 *  the same row rather than a fresh one. */
function fromAnnotation(a: Annotation): Box {
  return { id: a.id, class_id: a.class_id, bbox_json: a.bbox_json, attributes: a.attributes };
}

/**
 * Draw boxes on a full frame, and save them as the frame's complete set.
 *
 * Every coordinate this component stores is a full-frame pixel. The
 * overlay is an SVG whose viewBox *is* the frame, stretched over the
 * image, so boxes are drawn in frame pixels and the browser does the
 * scaling - there is no rendered-size to keep in sync. Mouse positions
 * are mapped through the image's rectangle on every event rather than
 * cached, so a resize mid-drag cannot skew a box.
 *
 * A drag is tracked on the window once it starts, so leaving the image
 * does not cancel it - the box just clamps to the edge.
 *
 * This is the thinnest slice: draw, save, reload. A new box has no class
 * yet; choosing one, and moving, resizing and deleting boxes, come next.
 * Saving null rather than guessing a class means nothing is recorded as
 * something it is not.
 */
function LabelCanvas({ frame, onSaved }: Props) {
  const [boxes, setBoxes] = useState<Box[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  // The window listeners read the draft from here so they never see a
  // stale closure between one mouse event and the next.
  const draftRef = useRef<Draft | null>(null);

  function describe(e: unknown): string {
    return e instanceof ApiError ? e.message : String(e);
  }

  useEffect(() => {
    let cancelled = false;
    api
      .getFrameAnnotations(frame.id)
      .then((annotations) => {
        if (cancelled) return;
        setBoxes(annotations.map(fromAnnotation));
        setDirty(false);
        setError(null);
      })
      .catch((e) => !cancelled && setError(describe(e)));
    return () => {
      cancelled = true;
    };
  }, [frame.id]);

  /** Mouse position -> full-frame pixels, clamped to the frame. */
  function toFrame(clientX: number, clientY: number): { x: number; y: number } {
    const rect = imgRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0 || rect.height === 0) return { x: 0, y: 0 };
    return {
      x: clamp(((clientX - rect.left) / rect.width) * frame.width, 0, frame.width),
      y: clamp(((clientY - rect.top) / rect.height) * frame.height, 0, frame.height),
    };
  }

  function updateDraft(next: Draft | null) {
    draftRef.current = next;
    setDraft(next);
  }

  function handleMouseDown(e: React.MouseEvent) {
    if (e.button !== 0) return;
    e.preventDefault();
    const p = toFrame(e.clientX, e.clientY);
    updateDraft({ x1: p.x, y1: p.y, x2: p.x, y2: p.y });
  }

  function finish(d: Draft) {
    updateDraft(null);
    const [x1, y1, x2, y2] = normalise(d);
    if (x2 - x1 < MIN_BOX_SIDE || y2 - y1 < MIN_BOX_SIDE) return;
    setBoxes((prev) => [...prev, { id: null, class_id: null, bbox_json: [x1, y1, x2, y2], attributes: {} }]);
    setDirty(true);
  }

  const drawing = draft !== null;
  useEffect(() => {
    if (!drawing) return;
    const move = (e: MouseEvent) => {
      const d = draftRef.current;
      if (!d) return;
      const p = toFrame(e.clientX, e.clientY);
      updateDraft({ ...d, x2: p.x, y2: p.y });
    };
    const up = (e: MouseEvent) => {
      const d = draftRef.current;
      if (!d) return;
      const p = toFrame(e.clientX, e.clientY);
      finish({ ...d, x2: p.x, y2: p.y });
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    return () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    // frame and the setters are stable for the life of this instance.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drawing]);

  async function save() {
    setSaving(true);
    try {
      const saved = await api.saveFrameAnnotations(frame.id, boxes);
      setBoxes(saved.map(fromAnnotation));
      setDirty(false);
      setError(null);
      onSaved?.({ ...frame, status: "labeled" });
    } catch (e) {
      setError(describe(e));
    } finally {
      setSaving(false);
    }
  }

  const draftBox = draft ? normalise(draft) : null;
  const unclassified = boxes.filter((b) => b.class_id === null).length;

  return (
    <div className="label-canvas">
      <div className="label-canvas__stage" data-testid="label-stage" onMouseDown={handleMouseDown}>
        <img ref={imgRef} src={api.fullFrameImageUrl(frame.id)} alt={`Frame ${frame.frame_index}`} draggable={false} />
        <svg
          className="label-canvas__overlay"
          viewBox={`0 0 ${frame.width} ${frame.height}`}
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          {boxes.map((box, index) => (
            <rect
              key={box.id ?? `new-${index}`}
              data-testid="label-box"
              x={box.bbox_json[0]}
              y={box.bbox_json[1]}
              width={box.bbox_json[2] - box.bbox_json[0]}
              height={box.bbox_json[3] - box.bbox_json[1]}
              vectorEffect="non-scaling-stroke"
            />
          ))}
          {draftBox && (
            <rect
              className="label-canvas__draft"
              data-testid="label-draft"
              x={draftBox[0]}
              y={draftBox[1]}
              width={draftBox[2] - draftBox[0]}
              height={draftBox[3] - draftBox[1]}
              vectorEffect="non-scaling-stroke"
            />
          )}
        </svg>
      </div>

      <div className="label-canvas__bar">
        <span>
          frame {frame.frame_index} &middot; {boxes.length} box{boxes.length === 1 ? "" : "es"}
          {dirty && " (unsaved)"}
        </span>
        {unclassified > 0 && (
          <span className="label-canvas__hint">
            {unclassified} without a class yet
          </span>
        )}
        <span className="label-canvas__hint">Drag on the image to draw a box.</span>
        <button className="btn-primary" onClick={save} disabled={saving || !dirty}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>

      {error && (
        <p className="label-canvas__error" role="alert">
          <IconAlert /> {error}
        </p>
      )}
    </div>
  );
}

export default LabelCanvas;
