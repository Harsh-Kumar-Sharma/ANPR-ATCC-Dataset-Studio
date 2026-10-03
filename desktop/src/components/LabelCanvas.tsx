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
  PlateReading,
  Project,
  ProjectClass,
  SuggestedBox,
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
  /** Called after the frame is deleted for good. Separate from
   *  onRejected because the frame no longer exists - there is nothing
   *  left to put back. */
  onDeleted?: (frameId: string) => void;
  /** Moving between frames, shown in the toolbar with the other actions. */
  nav?: React.ReactNode;
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

/** A model's box as an unsaved one. No id: it is not a row yet, and
 *  saving is what makes it one. */
function fromSuggestion(s: SuggestedBox): Box {
  return { id: null, class_id: null, bbox_json: s.bbox_json as Bbox, attributes: {} };
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
function LabelCanvas({
  project,
  frame,
  classesVersion = 0,
  onSaved,
  onDirtyChange,
  onRejected,
  onDeleted,
  nav,
}: Props) {
  const [boxes, setBoxes] = useState<Box[]>([]);
  const [classes, setClasses] = useState<ProjectClass[]>([]);
  const [attributeDefs, setAttributeDefs] = useState<AttributeDefinition[]>([]);
  const [plateReadings, setPlateReadings] = useState<PlateReading[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [dirty, setDirty] = useState(false);
  // How many of the boxes on screen came from the model and have
  // not been saved. Shown so nobody mistakes a suggestion for work
  // someone already did.
  const [suggested, setSuggested] = useState(0);
  // Whether the user has touched this frame since it opened, read by
  // the loader from a ref because it lands after that decision was
  // made.
  const working = useRef(false);

  /** The user has changed something. Both the flag the rest of the
   *  app reads and the ref the loader checks. */
  function markWorked() {
    working.current = true;
    setDirty(true);
  }
  const [saving, setSaving] = useState(false);
  const [skipping, setSkipping] = useState(false);
  const [confirmingSkip, setConfirmingSkip] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  // The frame's on-screen size: as large as fits the space left, at the
  // frame's own proportions. Null where nothing can be measured (a test
  // without layout), and the image then keeps its natural size.
  const [fitted, setFitted] = useState<{ width: number; height: number } | null>(null);
  // The full-size image is decoded and written on the server the first
  // time it is asked for, so it can fail where the thumbnail did not -
  // a full disk, most often. Without saying so the frame is just black.
  const [imageFailed, setImageFailed] = useState(false);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport || typeof ResizeObserver === "undefined") return;
    const fit = () => {
      const { clientWidth, clientHeight } = viewport;
      if (clientWidth <= 0 || clientHeight <= 0 || frame.width <= 0 || frame.height <= 0) return;
      const scale = Math.min(clientWidth / frame.width, clientHeight / frame.height);
      setFitted({ width: Math.floor(frame.width * scale), height: Math.floor(frame.height * scale) });
    };
    fit();
    const observer = new ResizeObserver(fit);
    observer.observe(viewport);
    return () => observer.disconnect();
  }, [frame.width, frame.height]);
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
    working.current = false;
    // Both at once, and written once. Fetching the suggestions after
    // the annotations left a second window in which they landed on
    // top of a box the user had already drawn and wiped it out.
    //
    // Suggestions are what the model found. They are not labels:
    // nothing is written until Save, which makes correcting one the
    // same gesture as accepting it. A failure to fetch them leaves an
    // empty canvas rather than an error - drawing by hand still works.
    Promise.all([
      api.getFrameAnnotations(frame.id),
      api.getFrameSuggestions(frame.id).catch(() => []),
    ])
      .then(([annotations, suggestions]) => {
        if (cancelled) return;
        // Someone started drawing while this was in flight. Their
        // work wins: overwriting it with what the frame looked like
        // before they touched it is the worst thing this could do.
        if (working.current) return;
        // A frame the model detected a plate in used to open
        // completely empty, and had to be drawn from scratch next to
        // a review screen that already knew where the plate was.
        const startFrom = annotations.length > 0 ? annotations.map(fromAnnotation) : suggestions.map(fromSuggestion);
        setBoxes(startFrom);
        setSuggested(annotations.length > 0 ? 0 : suggestions.length);
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

  // What the model read on this frame, so a labeller is not retyping a
  // plate it already has. Per frame, like the image. A failure leaves
  // the list empty rather than taking the canvas down - suggestions are
  // a convenience, boxes are the job.
  useEffect(() => {
    let cancelled = false;
    api
      .getFramePlateReadings(frame.id)
      .then((list) => !cancelled && setPlateReadings(list))
      .catch(() => !cancelled && setPlateReadings([]));
    return () => {
      cancelled = true;
    };
  }, [frame.id]);

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
    markWorked();
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
      markWorked();
      return;
    }
    const final =
      d.kind === "move"
        ? shifted(d.origin, px - d.startX, py - d.startY, frame.width, frame.height)
        : resized(d.origin, d.handle, px, py, frame.width, frame.height);
    setBox(d.index, final);
    if (!sameBox(final, d.origin)) markWorked();
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
    markWorked();
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
    markWorked();
  }

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      // Ctrl/Cmd+S first, and deliberately before the typing guard:
      // everywhere else it saves from inside a text field, and the
      // attributes panel is where a user now does real typing. Bare "S"
      // stays blocked - it belongs in the plate they are halfway
      // through, not in a save.
      if ((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")) {
        e.preventDefault();
        if (dirty && !saving) save();
        return;
      }
      if (isEditableTarget(e.target)) return;
      // Ctrl/Cmd with an arrow moves between frames, not pixels. That
      // belongs to the nav strip below the canvas; nudging here too
      // would shift the box on the way out.
      if (e.ctrlKey || e.metaKey) return;
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
    // A box drawn a moment ago has no id yet, so matching by id would
    // deselect it the instant it is saved - which closes the attributes
    // panel under someone who has just typed a plate into it. Its
    // position in the sent order is preserved by the reordering below,
    // so that is what to fall back to.
    const selectedIndex = selected;
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
      const byIdIndex = selectedId !== null ? ordered.findIndex((a) => a.id === selectedId) : -1;
      const fallback = selectedIndex !== null && selectedIndex < ordered.length ? selectedIndex : null;
      setSelected(byIdIndex >= 0 ? byIdIndex : fallback);
      setDirty(false);
    } catch (e) {
      setError(describe(e));
    } finally {
      setSaving(false);
    }
  }

  async function deleteForGood() {
    // Always asks, even with nothing drawn. Skipping can be undone by
    // putting the frame back; this cannot be undone at all, and the
    // two controls sit next to each other.
    if (!confirmingDelete) {
      setConfirmingDelete(true);
      return;
    }
    setConfirmingDelete(false);
    setDeleting(true);
    try {
      await api.deleteFrame(frame.id);
      setError(null);
      onDeleted?.(frame.id);
    } catch (e) {
      // The refusal worth reading is "this frame is in dataset version
      // vN", which names what is holding it.
      setError(describe(e));
    } finally {
      setDeleting(false);
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
      {/* Every action on one line above the frame, so none of them is
          below the fold of a tall image. */}
      <div className="label-canvas__bar">
        <span className="label-canvas__info">
          frame {frame.frame_index} &middot; {boxes.length} box{boxes.length === 1 ? "" : "es"}
          {/* Said plainly: these are the model's, nothing is saved,
              and the class is still yours to pick. */}
          {suggested > 0 && dirty === false && (
            <span className="label-canvas__suggested">
              {" "}&middot; {suggested} from the model - check, set the class, then Save
            </span>
          )}
          {dirty && " (unsaved)"}
          {unclassified > 0 && <span className="label-canvas__hint"> &middot; {unclassified} without a class yet</span>}
        </span>
        {nav && <div className="label-canvas__nav">{nav}</div>}
        <span className="label-canvas__actions">
          <button
            onClick={reject}
            disabled={saving || skipping || deleting}
            title="Not worth labelling - set it aside. This can be undone."
          >
            {confirmingSkip ? "Skip and lose boxes" : "Skip"}
          </button>
          {confirmingSkip && (
            <button onClick={() => setConfirmingSkip(false)} title="Keep working on this frame">
              Cancel
            </button>
          )}
          <button
            className="label-canvas__delete"
            onClick={deleteForGood}
            disabled={saving || skipping || deleting}
            title="Remove this frame and its image from disk. This cannot be undone."
          >
            {confirmingDelete ? "Delete for good" : "Delete"}
          </button>
          {confirmingDelete && (
            <button onClick={() => setConfirmingDelete(false)} title="Keep this frame">
              Cancel
            </button>
          )}
          <button className="btn-primary" onClick={save} disabled={saving || !dirty}>
            {saving ? "Saving…" : "Save"}
          </button>
        </span>
      </div>

      {error && (
        <p className="label-canvas__error" role="alert">
          <IconAlert /> {error}
        </p>
      )}

      <div className="label-canvas__body">
        {/* The frame is fitted into whatever space is left, so the page
            never scrolls and the next frame lands in the same place. */}
        <div className="label-canvas__viewport" ref={viewportRef}>
          <div
            className="label-canvas__stage"
            data-testid="label-stage"
            onMouseDown={startDraw}
            style={fitted ? { width: fitted.width, height: fitted.height } : undefined}
          >
            <img
              ref={imgRef}
              src={api.fullFrameImageUrl(frame.id)}
              alt={`Frame ${frame.frame_index}`}
              draggable={false}
              onError={() => setImageFailed(true)}
              onLoad={() => setImageFailed(false)}
            />
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
                  {Object.keys(box.attributes ?? {}).length > 0 && (
                    // Otherwise the only way to find out which of four
                    // vehicles already has a plate on it is to click all
                    // four.
                    <circle
                      className="label-canvas__has-attributes"
                      data-testid="label-has-attributes"
                      cx={box.bbox_json[2] - labelSize * 0.4}
                      cy={box.bbox_json[1] + labelSize * 0.4}
                      r={labelSize * 0.22}
                    />
                  )}
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
          {imageFailed && (
            <div className="label-canvas__image-error" role="alert">
              <IconAlert />
              <p>
                This frame's image could not be loaded. The server makes it from the video the first time it is opened,
                so this usually means its disk is full or the source video is missing. Free some space (Dataset →
                Storage), then open the frame again.
              </p>
            </div>
          )}
        </div>

        {/* Beside the frame rather than under it: choosing a box must
            not push the image around. */}
        <aside className="label-canvas__side">
          <div className="label-canvas__side-title">Selected box</div>
          {selectedBox && selected !== null ? (
            <div className="label-canvas__selected">
              <div className="label-canvas__selected-head">
                <span>
                  Box {selected + 1} of {boxes.length}
                </span>
                <button aria-label="Delete selected box" onClick={deleteSelected}>
                  Delete
                </button>
              </div>
              <label className="label-canvas__field">
                Class
                <select
                  aria-label="Class of selected box"
                  value={selectedBox.class_id === null ? "" : String(selectedBox.class_id)}
                  onChange={(e) => assignClass(e.target.value === "" ? null : Number(e.target.value))}
                >
                  <option value="">(none)</option>
                  {classes.map((c, i) => (
                    <option key={c.id} value={String(c.id)}>
                      {i < 9 ? `${i + 1} · ` : ""}
                      {c.name}
                    </option>
                  ))}
                </select>
              </label>

              {attributeDefs.length > 0 && (
                <div className="label-canvas__attributes" data-testid="label-attributes">
                  {attributeDefs.map((definition) => {
                    const value = (selectedBox.attributes ?? {})[definition.key];
                    if (definition.type === "boolean") {
                      return (
                        <label key={definition.key} className="label-canvas__attribute label-canvas__attribute--check">
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
                          maxLength={definition.max_length ?? undefined}
                          placeholder={definition.placeholder ?? undefined}
                          onChange={(e) => setAttribute(definition.key, e.target.value)}
                        />
                      </label>
                    );
                  })}
                </div>
              )}

              {plateReadings.length > 0 && attributeDefs.some((d) => d.key === "plate_text") && (
                // Every reading on the frame, not only the ones overlapping
                // this box: matching them would mean a second copy of the
                // association threshold active-learning owns, and three
                // lines is a list a person can simply read.
                <div className="label-canvas__plate-readings" data-testid="plate-readings">
                  <span>Model read:</span>
                  {plateReadings.map((reading) => (
                    <button
                      key={reading.normalized_text}
                      onClick={() => setAttribute("plate_text", reading.normalized_text)}
                      title={`${(reading.confidence * 100).toFixed(0)}% confident`}
                    >
                      {reading.normalized_text || "(no text)"}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <p className="label-canvas__side-empty">
              {boxes.length === 0 ? "Drag on the frame to draw a box." : "Click a box, or press ] to pick one."}
            </p>
          )}

          <div className="label-canvas__side-title">Shortcuts</div>
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
            {nav && (
              <span>
                <span className="kbd">Ctrl</span>
                <span className="kbd">&larr;</span>/<span className="kbd">&rarr;</span> frame
              </span>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}

export default LabelCanvas;
