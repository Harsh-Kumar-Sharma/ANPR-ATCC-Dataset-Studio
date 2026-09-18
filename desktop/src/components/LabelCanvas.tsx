import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import { IconAlert } from "../Icons";
import type { Frame, FrameAnnotationWrite, Project, ProjectClass } from "../types";

interface Props {
  project: Project;
  frame: Frame;
  /** Bumped by the class editor; the canvas reloads its class list. */
  classesVersion?: number;
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

/**
 * Draw boxes on a full frame, and save them as the frame's complete set.
 *
 * Every coordinate this component stores is a full-frame pixel. The
 * image on screen is scaled to fit, so mouse positions are mapped through
 * the image's rendered rectangle on every event rather than cached - a
 * resize between mousedown and mouseup then cannot skew a box.
 *
 * This is the thinnest slice: draw, save, reload. Moving, resizing,
 * deleting and choosing a class per box come next; a new box takes the
 * project's first class so it exports as something rather than nothing.
 */
function LabelCanvas({ project, frame, classesVersion = 0, onSaved }: Props) {
  const [boxes, setBoxes] = useState<Box[]>([]);
  const [classes, setClasses] = useState<ProjectClass[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The image's on-screen size, for placing the overlay's rectangles.
  const [rendered, setRendered] = useState({ width: 0, height: 0 });
  const imgRef = useRef<HTMLImageElement>(null);

  function describe(e: unknown): string {
    return e instanceof ApiError ? e.message : String(e);
  }

  useEffect(() => {
    let cancelled = false;
    api
      .getFrameAnnotations(frame.id)
      .then((annotations) => {
        if (cancelled) return;
        setBoxes(
          annotations.map((a) => ({ class_id: a.class_id, bbox_json: a.bbox_json, attributes: a.attributes })),
        );
        setDirty(false);
        setError(null);
      })
      .catch((e) => !cancelled && setError(describe(e)));
    return () => {
      cancelled = true;
    };
  }, [frame.id]);

  useEffect(() => {
    api.getClassSchema(project.id).then(setClasses).catch((e) => setError(describe(e)));
  }, [project.id, classesVersion]);

  const measure = useCallback(() => {
    const el = imgRef.current;
    if (el) setRendered({ width: el.clientWidth, height: el.clientHeight });
  }, []);

  useEffect(() => {
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [measure]);

  /** Mouse position -> full-frame pixels, clamped to the frame. */
  function toFrame(e: React.MouseEvent): { x: number; y: number } {
    const el = imgRef.current;
    if (!el) return { x: 0, y: 0 };
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return { x: 0, y: 0 };
    return {
      x: clamp(((e.clientX - rect.left) / rect.width) * frame.width, 0, frame.width),
      y: clamp(((e.clientY - rect.top) / rect.height) * frame.height, 0, frame.height),
    };
  }

  function handleMouseDown(e: React.MouseEvent) {
    if (e.button !== 0) return;
    const p = toFrame(e);
    setDraft({ x1: p.x, y1: p.y, x2: p.x, y2: p.y });
  }

  function handleMouseMove(e: React.MouseEvent) {
    if (!draft) return;
    const p = toFrame(e);
    setDraft({ ...draft, x2: p.x, y2: p.y });
  }

  function handleMouseUp() {
    if (!draft) return;
    const x1 = Math.min(draft.x1, draft.x2);
    const y1 = Math.min(draft.y1, draft.y2);
    const x2 = Math.max(draft.x1, draft.x2);
    const y2 = Math.max(draft.y1, draft.y2);
    setDraft(null);
    if (x2 - x1 < MIN_BOX_SIDE || y2 - y1 < MIN_BOX_SIDE) return;
    setBoxes((prev) => [...prev, { class_id: classes[0]?.id ?? null, bbox_json: [x1, y1, x2, y2], attributes: {} }]);
    setDirty(true);
  }

  async function save() {
    setSaving(true);
    try {
      const saved = await api.saveFrameAnnotations(frame.id, boxes);
      setBoxes(saved.map((a) => ({ class_id: a.class_id, bbox_json: a.bbox_json, attributes: a.attributes })));
      setDirty(false);
      setError(null);
      onSaved?.({ ...frame, status: "labeled" });
    } catch (e) {
      setError(describe(e));
    } finally {
      setSaving(false);
    }
  }

  // Full-frame pixels -> on-screen pixels for the overlay.
  const sx = frame.width ? rendered.width / frame.width : 0;
  const sy = frame.height ? rendered.height / frame.height : 0;
  const toScreen = (b: [number, number, number, number]) => ({
    x: b[0] * sx,
    y: b[1] * sy,
    width: (b[2] - b[0]) * sx,
    height: (b[3] - b[1]) * sy,
  });
  const draftBox: [number, number, number, number] | null = draft
    ? [Math.min(draft.x1, draft.x2), Math.min(draft.y1, draft.y2), Math.max(draft.x1, draft.x2), Math.max(draft.y1, draft.y2)]
    : null;

  return (
    <div className="label-canvas">
      <div
        className="label-canvas__stage"
        data-testid="label-stage"
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={() => setDraft(null)}
      >
        <img
          ref={imgRef}
          src={api.fullFrameImageUrl(frame.id)}
          alt={`Frame ${frame.frame_index}`}
          draggable={false}
          onLoad={measure}
        />
        <svg className="label-canvas__overlay" aria-hidden="true">
          {boxes.map((box, index) => (
            <rect key={index} data-testid="label-box" {...toScreen(box.bbox_json)} />
          ))}
          {draftBox && <rect className="label-canvas__draft" data-testid="label-draft" {...toScreen(draftBox)} />}
        </svg>
      </div>

      <div className="label-canvas__bar">
        <span>
          frame {frame.frame_index} &middot; {boxes.length} box{boxes.length === 1 ? "" : "es"}
          {dirty && " (unsaved)"}
        </span>
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
