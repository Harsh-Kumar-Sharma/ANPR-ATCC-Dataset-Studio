import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";
import type { Frame, Project, QueueProgress, SourceQueue } from "./types";

/** Where the user last was, per project. Browser storage rather than the
 *  database: it is this machine's view of a shared project, and losing
 *  it costs a scroll, not work. */
const lastFrameKey = (projectId: string) => `anpr:last-frame:${projectId}`;

function readLastFrame(projectId: string): string | null {
  try {
    return window.localStorage.getItem(lastFrameKey(projectId));
  } catch {
    return null;
  }
}

function writeLastFrame(projectId: string, frameId: string): void {
  try {
    window.localStorage.setItem(lastFrameKey(projectId), frameId);
  } catch {
    // Private mode, or storage full. Not worth interrupting labelling.
  }
}

/** Which source the user was working through, per project. Remembered
 *  for the same reason as the frame: labelling a clip is a session that
 *  spans restarts, and re-picking it every time is friction. */
const lastSourceKey = (projectId: string) => `anpr:last-source:${projectId}`;

function readLastSource(projectId: string): string | null {
  try {
    return window.localStorage.getItem(lastSourceKey(projectId));
  } catch {
    return null;
  }
}

function writeLastSource(projectId: string, sourceId: string | null): void {
  try {
    if (sourceId === null) window.localStorage.removeItem(lastSourceKey(projectId));
    else window.localStorage.setItem(lastSourceKey(projectId), sourceId);
  } catch {
    // As above.
  }
}

/** Who asked to move, so the "unsaved boxes" question appears where the
 *  user is looking rather than in the panel they are not. */
export type Asker = "queue" | "canvas";

export interface FrameQueue {
  frames: Frame[];
  progress: QueueProgress | null;
  sources: SourceQueue[];
  sourceId: string | null;
  showSetAside: boolean;
  error: string | null;
  /** The open frame's place in the list, or -1 when none is open. */
  index: number;
  /** The frame a move is waiting on, and who asked for the move. */
  pending: { frame: Frame; from: Asker } | null;
  chooseSource: (sourceId: string | null) => void;
  setShowSetAside: (show: boolean) => void;
  refresh: () => Promise<Frame[] | null>;
  /** Open a frame, asking first if the open one has unsaved boxes. */
  open: (frame: Frame, from?: Asker) => void;
  step: (by: 1 | -1, from?: Asker) => void;
  /** True when there is a frame that way to step to. */
  canStep: (by: 1 | -1) => boolean;
  /** Answer the unsaved-boxes question: discard and move, or stay. */
  resolvePending: (discard: boolean) => void;
}

interface Options {
  /** Null while no project is open. The hook still runs - hooks must -
   *  but asks for nothing and answers with an empty queue. */
  project: Project | null;
  selectedFrameId: string | null;
  /** True while the open frame has boxes that have not been saved. */
  dirty?: boolean;
  /** Bumped when a frame is saved or skipped, so the list catches up. */
  refreshKey?: number;
  /** Narrow to one source. Set when the source was picked elsewhere -
   *  clicking one in the Sources panel - which wins over whatever was
   *  remembered. Undefined means nobody outside has an opinion. */
  sourceId?: string | null;
  onSelect: (frame: Frame) => void;
  onSourceChange?: (sourceId: string | null) => void;
  /** Reopen the frame last worked on when the queue first loads. Off
   *  when the app already knows where the user is - the URL said. */
  resume?: boolean;
}

/**
 * The frames waiting to be labelled, and how to move between them.
 *
 * Held here rather than in the queue panel because two places need the
 * same list: the sidebar, and the Previous/Next under the canvas. Two
 * copies would be two lists that disagree about which frame is next.
 *
 * Navigation is guarded rather than blocked. Walking away from unsaved
 * boxes asks first, because the alternative is either losing work
 * silently or refusing to move at all, and both are worse than a
 * question.
 */
export function useFrameQueue({
  project,
  selectedFrameId,
  dirty = false,
  refreshKey = 0,
  sourceId: sourceIdProp,
  onSelect,
  onSourceChange,
  resume = true,
}: Options): FrameQueue {
  const [frames, setFrames] = useState<Frame[]>([]);
  const [progress, setProgress] = useState<QueueProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<{ frame: Frame; from: Asker } | null>(null);
  const [resumed, setResumed] = useState(false);
  // Frames out of the queue: rejected by a human, or passed over by
  // selection. Shown together because the question the user is asking
  // is the same one - what am I not being offered?
  const [showSetAside, setShowSetAside] = useState<boolean>(false);
  // Which source is being worked through; null is all of them. One
  // list across three videos is unreadable - you cannot tell whose
  // frames you are looking at, or finish one clip before the next.
  const [sources, setSources] = useState<SourceQueue[]>([]);
  const [sourceId, setSourceId] = useState<string | null>(() =>
    project ? (sourceIdProp ?? readLastSource(project.id)) : null,
  );
  // Only the newest request may write. Two refreshes racing (rapid saves
  // each bumping refreshKey) could otherwise land out of order and leave
  // the list describing an older state than the counts.
  const latestRequest = useRef(0);

  const describe = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

  const projectId = project?.id ?? null;

  const refresh = useCallback(async () => {
    if (projectId === null) return null;
    const request = ++latestRequest.current;
    try {
      // The sources first, and alone: a remembered source may have been
      // deleted since, and asking for its frames would answer 404 and
      // leave the tab showing an error it cannot be clicked out of.
      const perSource = await api.getQueueBySource(projectId);
      if (request !== latestRequest.current) return null;
      const known = sourceId !== null && perSource.some((s) => s.source_id === sourceId);
      const active = known ? sourceId : null;
      if (!known && sourceId !== null) {
        setSourceId(null);
        writeLastSource(projectId, null);
      }

      const [list, counts] = await Promise.all([
        showSetAside
          ? Promise.all([
              api.listFrames(projectId, "rejected", active),
              api.listFrames(projectId, "skipped", active),
            ]).then(([rejected, skipped]) =>
              [...rejected, ...skipped].sort((a, b) => a.frame_index - b.frame_index),
            )
          : api.listFrames(projectId, undefined, active),
        api.getQueueProgress(projectId, active),
      ]);
      if (request !== latestRequest.current) return null;
      setFrames(list);
      setProgress(counts);
      setSources(perSource);
      setError(null);
      return list;
    } catch (e) {
      if (request === latestRequest.current) setError(describe(e));
      return null;
    }
  }, [projectId, showSetAside, sourceId]);

  useEffect(() => {
    let cancelled = false;
    refresh().then((list) => {
      if (cancelled || !list || projectId === null) return;
      // Put the user back where they were, once, and only if they are
      // not already looking at something.
      if (!resume || resumed || selectedFrameId !== null) return;
      setResumed(true);
      const last = readLastFrame(projectId);
      const frame = list.find((f) => f.id === last);
      if (frame) onSelect(frame);
    });
    return () => {
      cancelled = true;
    };
    // onSelect and selectedFrameId are deliberately not dependencies:
    // resuming is a one-shot on arrival, not a reaction to selection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh, refreshKey]);

  useEffect(() => {
    if (projectId && selectedFrameId) writeLastFrame(projectId, selectedFrameId);
  }, [projectId, selectedFrameId]);

  // A source picked outside this panel wins over what was remembered,
  // and is remembered in turn: arriving here from the Sources panel
  // should leave the tab on that clip next time too.
  useEffect(() => {
    if (sourceIdProp === undefined || projectId === null) return;
    setSourceId(sourceIdProp);
    writeLastSource(projectId, sourceIdProp);
  }, [projectId, sourceIdProp]);

  function chooseSource(next: string | null) {
    setSourceId(next);
    if (projectId) writeLastSource(projectId, next);
    onSourceChange?.(next);
  }

  const index = frames.findIndex((f) => f.id === selectedFrameId);

  function open(frame: Frame, from: Asker = "queue") {
    if (frame.id === selectedFrameId) return;
    if (dirty) {
      setPending({ frame, from });
      return;
    }
    onSelect(frame);
  }

  /** The frame one step away, or undefined at the end.
   *
   *  With nothing open, a step lands on the first or last frame rather
   *  than doing nothing: the user asked to move somewhere. */
  function neighbour(by: 1 | -1): Frame | undefined {
    if (index === -1) return frames[by === 1 ? 0 : frames.length - 1];
    return frames[index + by];
  }

  function step(by: 1 | -1, from: Asker = "queue") {
    const next = neighbour(by);
    if (next) open(next, from);
  }

  function canStep(by: 1 | -1): boolean {
    return neighbour(by) !== undefined;
  }

  function resolvePending(discard: boolean) {
    const waiting = pending;
    setPending(null);
    if (discard && waiting) onSelect(waiting.frame);
  }

  return {
    frames,
    progress,
    sources,
    sourceId,
    showSetAside,
    error,
    index,
    pending,
    chooseSource,
    setShowSetAside,
    refresh,
    open,
    step,
    canStep,
    resolvePending,
  };
}
