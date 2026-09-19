import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import { IconAlert } from "../Icons";
import { isEditableTarget } from "../keyboard";
import type {
  Annotation,
  AttributeDefinition,
  Bbox,
  Frame,
  FrameAnnotationWrite,
  Project,
  ProjectClass,
} from "../types";

interface Props {
  project: Project;
  frame: Frame;
  /** Bumped by the class editor; the canvas reloads its class list. */
  classesVersion?: number;
  onSaved?: (frame: Frame) => void;
  /** Told whenever the frame gains or loses unsaved boxes, so the
   *  queue can refuse to walk away from them silently. */
  onDirtyChange?: (dirty: boolean) => void;
  /** Called after the frame is skipped, so the queue moves on. */
  onRejected?: (frame: Frame) => void;
}

type Box = FrameAnnotationWrite;

type Handle = "nw" | "n" | "ne" | "e" | "se" | "s" | "sw" | "w";
const HANDLES: Handle[] = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];

/** What the mouse is doing, in full-frame pixels. Only one of these is
 *  ever in flight, and the window listeners read it from a ref so they
 *  never see a stale closure between one event and the next. */
type Drag =
  | { kind: "draw"; x1: number; y1: number; x2: number; y2: number }
  | { kind: "move"; index: number; startX: number; startY: number; origin: Bbox }
  | { kind: "resize"; index: number; handle: Handle; origin: Bbox };

/** Boxes smaller than this in either dimension are accidental clicks,
 *  not labels. In full-frame pixels. */
const MIN_BOX_SIDE = 2;
const NUDGE = 1;
const NUDGE_FAST = 10;

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

function normalise(b: Bbox): Bbox {
  return [Math.min(b[0], b[2]), Math.min(b[1], b[3]), Math.max(b[0], b[2]), Math.max(b[1], b[3])];
}

/** Shift a box without letting it leave the frame - the shift is limited,
 *  the box is never squashed. */
function shifted(b: Bbox, dx: number, dy: number, width: number, height: number): Bbox {
  const sx = clamp(dx, -b[0], width - b[2]);
  const sy = clamp(dy, -b[1], height - b[3]);
  return [b[0] + sx, b[1] + sy, b[2] + sx, b[3] + sy];
}

/** Keep a dragged edge at least MIN_BOX_SIDE from the edge it pivots on.
 *
 *  Crossing over is still allowed - that is the flip - but the moving
 *  edge cannot land on the anchor, which would make a box of no area
 *  that the server rejects on save and the user cannot see to fix. If
 *  the minimum does not fit on the side being approached, it goes to
 *  the other side, so a box pinned against the frame edge still resists
 *  rather than collapsing.
 */
function withMinimumSpan(anchor: number, moving: number, limit: number): number {
  const next = moving >= anchor ? Math.max(moving, anchor + MIN_BOX_SIDE) : Math.min(moving, anchor - MIN_BOX_SIDE);
  if (next < 0) return Math.min(anchor + MIN_BOX_SIDE, limit);
  if (next > limit) return Math.max(anchor - MIN_BOX_SIDE, 0);
  return next;
}

/** Move the edge(s) a handle names to the pointer, then normalise so
 *  dragging past the opposite edge flips the box instead of inverting. */
function resized(origin: Bbox, handle: Handle, px: number, py: number, width: number, height: number): Bbox {
  const [x1, y1, x2, y2] = origin;
  // The anchor is the edge that is not moving - the box pivots on it.
  const nx = handle.includes("w")
    ? withMinimumSpan(x2, px, width)
    : handle.includes("e")
      ? withMinimumSpan(x1, px, width)
      : null;
  const ny = handle.includes("n")
    ? withMinimumSpan(y2, py, height)
    : handle.includes("s")
      ? withMinimumSpan(y1, py, height)
      : null;
  const next: Bbox = [
    handle.includes("w") ? (nx as number) : x1,
    handle.includes("n") ? (ny as number) : y1,
    handle.includes("e") ? (nx as number) : x2,
    handle.includes("s") ? (ny as number) : y2,
  ];
  return normalise(next);
}

function sameBox(a: Bbox, b: Bbox): boolean {
  return a[0] === b[0] && a[1] === b[1] && a[2] === b[2] && a[3] === b[3];
}

/** A loaded annotation, carried with its id so saving it back keeps it
 *  the same row rather than a fresh one. */
function fromAnnotation(a: Annotation): Box {
  return { id: a.id, class_id: a.class_id, bbox_json: a.bbox_json, attributes: a.attributes };
}

/**
 * Label a frame: draw boxes, move and resize them, give each a class,
 * and save the lot as the frame's complete set - from the keyboard
 * wherever it matters.
 *
 * Every coordinate this component stores is a full-frame pixel. The
 * overlay is an SVG whose viewBox *is* the frame, stretched over the
 * image, so geometry is drawn in frame pixels and the browser scales
 * it. Mouse positions are mapped through the image's rectangle on every
 * event rather than cached, so a resize mid-drag cannot skew a box.
 *
 * A drag is tracked on the window once it starts, so leaving the image
 * does not cancel it - the box just clamps to the edge.
 *
 * Shortcuts are on the window, like the review panel's, and are
 * ignored while typing into a field. Number keys assign a class by its
 * position in the project's list, which is what makes a frame with
 * four vehicles labellable without touching the mouse after drawing.
 */
function LabelCanvas({ project, frame, classesVersion = 0, onSaved, onDirtyChange, onRejected }: Props) {
  const [boxes, setBoxes] = useState<Box[]>([]);
  const [classes, setClasses] = useState<ProjectClass[]>([]);
  const [attributeDefs, setAttributeDefs] = useState<AttributeDefinition[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [skipping, setSkipping] = useState(false);
  const [confirmingSkip, setConfirmingSkip] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const dragRef = useRef<Drag | null>(null);
  // Mirrors `boxes` so a mouseup can append and select in one step.
  const boxesRef = useRef<Box[]>([]);
  useEffect(() => {
    boxesRef.current = boxes;
  }, [boxes]);

  function describe(e: unknown): string {
    return e instanceof ApiError ? e.message : String(e);
  }

  useEffect(() => {
    onDirtyChange?.(dirty);
    // On the way out the work is gone, so nothing downstream should
    // still believe this frame has unsaved boxes - it would offer to
    // discard something that no longer exists.
    return () => onDirtyChange?.(false);
    // onDirtyChange is a callback prop; re-running on its identity
    // would fire on every parent render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dirty]);

  useEffect(() => {
    let cancelled = false;
    api
      .getFrameAnnotations(frame.id)
      .then((annotations) => {
        if (cancelled) return;
        setBoxes(annotations.map(fromAnnotation));
        setSelected(null);
        setDirty(false);
        setError(null);
      })
      .catch((e) => !cancelled && setError(describe(e)));
    return () => {
      cancelled = true;
    };
  }, [frame.id]);

  useEffect(() => {
    let cancelled = false;
    api
      .getClassSchema(project.id)
      .then((list) => !cancelled && setClasses(list))
      .catch((e) => !cancelled && setError(describe(e)));
    return () => {
      cancelled = true;
    };
  }, [project.id, classesVersion]);

  // Loaded once, not per frame: what a box can carry is a property of
  // the domain, not of the frame being looked at. A failure here leaves
  // the list empty and the panel unrendered rather than taking the
  // canvas down - boxes and classes are the job, attributes are extra.
  useEffect(() => {
    let cancelled = false;
    api
      .listAttributeDefinitions()
      .then((list) => !cancelled && setAttributeDefs(list))
      .catch(() => !cancelled && setAttributeDefs([]));
    return () => {
      cancelled = true;
    };
  }, []);

  /** Set one attribute on the selected box.
   *
   *  `undefined` clears it, and clearing removes the key rather than
   *  writing a blank - the server stores it that way, so sending it that
   *  way keeps what is on screen and what is in the database the same
   *  shape. */
  function setAttribute(key: string, value: string | boolean | undefined) {
    updateSelected((b) => {
      const next = { ...b.attributes };
      if (value === undefined || value === "") delete next[key];
      else next[key] = value;
      return { ...b, attributes: next };
    });
  }

  /** Mouse position -> full-frame pixels, clamped to the frame. */
  function toFrame(clientX: number, clientY: number): { x: number; y: number } {
    const rect = imgRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0 || rect.height === 0) return { x: 0, y: 0 };
    return {
      x: clamp(((clientX - rect.left) / rect.width) * frame.width, 0, frame.width),
      y: clamp(((clientY - rect.top) / rect.height) * frame.height, 0, frame.height),
    };
  }

  function updateDrag(next: Drag | null) {
    dragRef.current = next;
    setDrag(next);
  }

  /** Abandon a drag in progress, or deselect if there is none.
   *
   *  A move or resize has already written its geometry by the time this
   *  runs, so cancelling has to put the box back - otherwise it stays
   *  where it was dragged while Save sits disabled, and the edit is
   *  stranded somewhere the user cannot commit or undo.
   */
  function cancelDrag() {
    const d = dragRef.current;
    if (!d) {
      setSelected(null);
      return;
    }
    if (d.kind !== "draw") setBox(d.index, d.origin);
    updateDrag(null);
  }

  function setBox(index: number, bbox: Bbox) {
    setBoxes((prev) => prev.map((b, i) => (i === index ? { ...b, bbox_json: bbox } : b)));
  }

  function updateSelected(change: (box: Box) => Box) {
    if (selected === null) return;
    setBoxes((prev) => prev.map((b, i) => (i === selected ? change(b) : b)));
    setDirty(true);
  }

  // --- mouse ---------------------------------------------------------------

  function startDraw(e: React.MouseEvent) {
    if (e.button !== 0) return;
    e.preventDefault();
    const p = toFrame(e.clientX, e.clientY);
    setSelected(null);
    updateDrag({ kind: "draw", x1: p.x, y1: p.y, x2: p.x, y2: p.y });
  }

  function startMove(e: React.MouseEvent, index: number) {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const p = toFrame(e.clientX, e.clientY);
    setSelected(index);
    updateDrag({ kind: "move", index, startX: p.x, startY: p.y, origin: boxes[index].bbox_json });
  }

  function startResize(e: React.MouseEvent, index: number, handle: Handle) {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    updateDrag({ kind: "resize", index, handle, origin: boxes[index].bbox_json });
  }

  function apply(d: Drag, px: number, py: number): Drag {
    switch (d.kind) {
      case "draw":
        return { ...d, x2: px, y2: py };
      case "move":
        setBox(d.index, shifted(d.origin, px - d.startX, py - d.startY, frame.width, frame.height));
        return d;
      case "resize":
        setBox(d.index, resized(d.origin, d.handle, px, py, frame.width, frame.height));
        return d;
    }
  }

  function finish(d: Drag, px: number, py: number) {
    updateDrag(null);
    if (d.kind === "draw") {
      const [x1, y1, x2, y2] = normalise([d.x1, d.y1, px, py]);
      if (x2 - x1 < MIN_BOX_SIDE || y2 - y1 < MIN_BOX_SIDE) return;
      const next = [...boxesRef.current, { id: null, class_id: null, bbox_json: [x1, y1, x2, y2] as Bbox, attributes: {} }];
      setBoxes(next);
      setSelected(next.length - 1);
      setDirty(true);
      return;
    }
    const final =
      d.kind === "move"
        ? shifted(d.origin, px - d.startX, py - d.startY, frame.width, frame.height)
        : resized(d.origin, d.handle, px, py, frame.width, frame.height);
    setBox(d.index, final);
    if (!sameBox(final, d.origin)) setDirty(true);
  }

  const dragging = drag !== null;
  useEffect(() => {
    if (!dragging) return;
    const move = (e: MouseEvent) => {
      const d = dragRef.current;
      if (!d) return;
      const p = toFrame(e.clientX, e.clientY);
      updateDrag(apply(d, p.x, p.y));
    };
    const up = (e: MouseEvent) => {
      const d = dragRef.current;
      if (!d) return;
      const p = toFrame(e.clientX, e.clientY);
      finish(d, p.x, p.y);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    return () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    // frame and the setters are stable for the life of this instance.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dragging]);

  // --- keyboard ------------------------------------------------------------

  function cycle(direction: 1 | -1) {
    if (boxes.length === 0) return;
    if (selected === null) {
      setSelected(direction === 1 ? 0 : boxes.length - 1);
      return;
    }
    setSelected((selected + direction + boxes.length) % boxes.length);
  }

  function deleteSelected() {
    if (selected === null) return;
    const remaining = boxes.length - 1;
    setBoxes((prev) => prev.filter((_, i) => i !== selected));
    // Stay where the user was working rather than dropping them out of
    // the frame entirely: the box that slid into this slot, or the last
    // one if they deleted the end of the list.
    setSelected(remaining === 0 ? null : Math.min(selected, remaining - 1));
    setDirty(true);
  }

  function assignClass(classId: number | null) {
    updateSelected((b) => ({ ...b, class_id: classId }));
  }

  function nudge(dx: number, dy: number) {
    if (selected === null) return;
    const before = boxes[selected].bbox_json;
    const after = shifted(before, dx, dy, frame.width, frame.height);
    if (sameBox(before, after)) return;
    setBox(selected, after);
    setDirty(true);
  }

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (isEditableTarget(e.target)) return;
      if ((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")) {
        e.preventDefault();
        if (dirty && !saving) save();
        return;
      }
      const step = e.shiftKey ? NUDGE_FAST : NUDGE;
      switch (e.key) {
        case "]":
          cycle(1);
          break;
        case "[":
          cycle(-1);
          break;
        case "Escape":
          cancelDrag();
          break;
        case "Delete":
        case "Backspace":
          if (selected !== null) e.preventDefault();
          deleteSelected();
          break;
        case "ArrowLeft":
          if (selected !== null) e.preventDefault();
          nudge(-step, 0);
          break;
        case "ArrowRight":
          if (selected !== null) e.preventDefault();
          nudge(step, 0);
          break;
        case "ArrowUp":
          if (selected !== null) e.preventDefault();
          nudge(0, -step);
          break;
        case "ArrowDown":
          if (selected !== null) e.preventDefault();
          nudge(0, step);
          break;
        case "s":
        case "S":
          if (dirty && !saving) save();
          break;
        case "0":
          if (selected !== null) assignClass(null);
          break;
        default: {
          // 1-9: the class at that position in the project's list.
          if (selected !== null && /^[1-9]$/.test(e.key)) {
            const cls = classes[Number(e.key) - 1];
            if (cls) assignClass(cls.id);
          }
        }
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
    // Re-registered every render so the handler always sees current
    // state - the same shape the review panel uses.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  });

  // --- saving --------------------------------------------------------------

  async function save() {
    // What is being sent, held by identity. Editing replaces the array,
    // so this is how the response knows whether it is still describing
    // what the user has on screen.
    const sent = boxes;
    const selectedId = selected !== null ? boxes[selected].id : null;
    setSaving(true);
    try {
      const saved = await api.saveFrameAnnotations(frame.id, sent);
      onSaved?.({ ...frame, status: "labeled" });
      setError(null);

      if (boxesRef.current !== sent) {
        // The user kept working while the request was out. Their boxes
        // win - adopting the response here would silently undo whatever
        // they just did, and the frame still has changes to send.
        return;
      }

      // Server order is by updated_at, so an edited box can come back in
      // a different position. Put them back in the order they were sent
      // so the canvas does not reshuffle under the user, with anything
      // new appended.
      const byId = new Map(saved.map((a) => [a.id, a]));
      const inSentOrder = sent.flatMap((box) => (box.id && byId.has(box.id) ? [byId.get(box.id)!] : []));
      const seen = new Set(inSentOrder.map((a) => a.id));
      const ordered = [...inSentOrder, ...saved.filter((a) => !seen.has(a.id))];

      setBoxes(ordered.map(fromAnnotation));
      setSelected(selectedId !== null ? ordered.findIndex((a) => a.id === selectedId) : null);
      setDirty(false);
    } catch (e) {
      setError(describe(e));
    } finally {
      setSaving(false);
    }
  }

  async function reject() {
    // Skipping throws away whatever is on the frame, so it asks first
    // for the same reason walking away from the frame does.
    if (dirty && !confirmingSkip) {
      setConfirmingSkip(true);
      return;
    }
    setConfirmingSkip(false);
    setSkipping(true);
    try {
      const updated = await api.setFrameStatus(frame.id, "rejected");
      setError(null);
      onRejected?.(updated);
    } catch (e) {
      setError(describe(e));
    } finally {
      setSkipping(false);
    }
  }

  // --- render --------------------------------------------------------------

  const classNameOf = (id: number | null) => classes.find((c) => c.id === id)?.name ?? "?";
  const draftBox: Bbox | null = drag?.kind === "draw" ? normalise([drag.x1, drag.y1, drag.x2, drag.y2]) : null;
  const unclassified = boxes.filter((b) => b.class_id === null).length;
  const selectedBox = selected !== null ? boxes[selected] : null;
  // Sized in frame pixels, as a fraction of the frame: the overlay is
  // scaled to fit, so a fixed number of frame units would render tiny on
  // a 1920px frame and huge on a 640px one. Same reasoning for both.
  const handleSize = Math.max(6, frame.width * 0.01);
  const labelSize = Math.max(11, frame.width * 0.016);

  function handlePoints(b: Bbox): Record<Handle, [number, number]> {
    const cx = (b[0] + b[2]) / 2;
    const cy = (b[1] + b[3]) / 2;
    return {
      nw: [b[0], b[1]],
      n: [cx, b[1]],
      ne: [b[2], b[1]],
      e: [b[2], cy],
      se: [b[2], b[3]],
      s: [cx, b[3]],
      sw: [b[0], b[3]],
      w: [b[0], cy],
    };
  }

  return (
    <div className="label-canvas">
      <div className="label-canvas__stage" data-testid="label-stage" onMouseDown={startDraw}>
        <img ref={imgRef} src={api.fullFrameImageUrl(frame.id)} alt={`Frame ${frame.frame_index}`} draggable={false} />
        <svg
          className="label-canvas__overlay"
          viewBox={`0 0 ${frame.width} ${frame.height}`}
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          {boxes.map((box, index) => (
            <g key={box.id ?? `new-${index}`}>
              <rect
                className="label-canvas__box"
                data-testid="label-box"
                data-selected={index === selected}
                x={box.bbox_json[0]}
                y={box.bbox_json[1]}
                width={box.bbox_json[2] - box.bbox_json[0]}
                height={box.bbox_json[3] - box.bbox_json[1]}
                onMouseDown={(e) => startMove(e, index)}
              />
              <text
                className="label-canvas__class"
                data-testid="label-class"
                x={box.bbox_json[0] + labelSize * 0.3}
                y={box.bbox_json[1] + labelSize * 1.1}
                style={{ fontSize: labelSize, strokeWidth: labelSize * 0.25 }}
              >
                {classNameOf(box.class_id)}
              </text>
            </g>
          ))}
          {selectedBox &&
            selected !== null &&
            (() => {
              const points = handlePoints(selectedBox.bbox_json);
              return HANDLES.map((handle) => {
                const [hx, hy] = points[handle];
                return (
                  <rect
                    key={handle}
                    className="label-canvas__handle"
                    data-testid="label-handle"
                    data-handle={handle}
                    x={hx - handleSize / 2}
                    y={hy - handleSize / 2}
                    width={handleSize}
                    height={handleSize}
                    onMouseDown={(e) => startResize(e, selected, handle)}
                  />
                );
              });
            })()}
          {draftBox && (
            <rect
              className="label-canvas__draft"
              data-testid="label-draft"
              x={draftBox[0]}
              y={draftBox[1]}
              width={draftBox[2] - draftBox[0]}
              height={draftBox[3] - draftBox[1]}
            />
          )}
        </svg>
      </div>

      <div className="label-canvas__bar">
        <span>
          frame {frame.frame_index} &middot; {boxes.length} box{boxes.length === 1 ? "" : "es"}
          {dirty && " (unsaved)"}
        </span>
        {unclassified > 0 && <span className="label-canvas__hint">{unclassified} without a class yet</span>}
        <button onClick={reject} disabled={saving || skipping} title="Not worth labelling - skip it">
          {confirmingSkip ? "Skip and lose boxes" : "Skip"}
        </button>
        {confirmingSkip && (
          <button onClick={() => setConfirmingSkip(false)} title="Keep working on this frame">
            Cancel
          </button>
        )}
        <button className="btn-primary" onClick={save} disabled={saving || !dirty}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>

      {selectedBox && selected !== null && (
        <div className="label-canvas__selected">
          <span>
            Box {selected + 1} of {boxes.length}
          </span>
          <label>
            Class{" "}
            <select
              aria-label="Class of selected box"
              value={selectedBox.class_id === null ? "" : String(selectedBox.class_id)}
              onChange={(e) => assignClass(e.target.value === "" ? null : Number(e.target.value))}
            >
              <option value="">(none)</option>
              {classes.map((c) => (
                <option key={c.id} value={String(c.id)}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
          <button aria-label="Delete selected box" onClick={deleteSelected}>
            Delete
          </button>

          {attributeDefs.length > 0 && (
            <div className="label-canvas__attributes" data-testid="label-attributes">
              {attributeDefs.map((definition) => {
                const value = selectedBox.attributes[definition.key];
                if (definition.type === "boolean") {
                  return (
                    <label key={definition.key} className="label-canvas__attribute">
                      <input
                        type="checkbox"
                        aria-label={definition.label}
                        checked={value === true}
                        onChange={(e) => setAttribute(definition.key, e.target.checked || undefined)}
                      />
                      {definition.label}
                    </label>
                  );
                }
                if (definition.type === "choice") {
                  return (
                    <label key={definition.key} className="label-canvas__attribute">
                      {definition.label}{" "}
                      <select
                        aria-label={definition.label}
                        value={typeof value === "string" ? value : ""}
                        onChange={(e) => setAttribute(definition.key, e.target.value || undefined)}
                      >
                        <option value="">(none)</option>
                        {(definition.options ?? []).map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                    </label>
                  );
                }
                return (
                  <label key={definition.key} className="label-canvas__attribute">
                    {definition.label}{" "}
                    <input
                      type="text"
                      aria-label={definition.label}
                      value={typeof value === "string" ? value : ""}
                      maxLength={definition.max_length}
                      placeholder={definition.placeholder}
                      onChange={(e) => setAttribute(definition.key, e.target.value)}
                    />
                  </label>
                );
              })}
            </div>
          )}
        </div>
      )}

      <div className="label-canvas__shortcuts" data-testid="label-shortcuts">
        <span>drag to draw</span>
        <span>
          <span className="kbd">[</span>/<span className="kbd">]</span> box
        </span>
        <span>
          <span className="kbd">1</span>–<span className="kbd">9</span> class
        </span>
        <span>
          <span className="kbd">0</span> none
        </span>
        <span>
          <span className="kbd">Del</span> delete
        </span>
        <span>
          <span className="kbd">&larr;&uarr;&rarr;&darr;</span> nudge (<span className="kbd">Shift</span> &times;10)
        </span>
        <span>
          <span className="kbd">S</span> save
        </span>
        <span>
          <span className="kbd">Esc</span> deselect
        </span>
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
