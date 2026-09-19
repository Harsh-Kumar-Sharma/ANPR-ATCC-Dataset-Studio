import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import { IconAlert, IconCheck, IconX } from "../Icons";
import { isEditableTarget } from "../keyboard";
import type { Bbox, FrameCandidate, OcrCandidate, Project, ProjectClass, Track, TrackTimeline } from "../types";

interface Props {
  project: Project;
  track: Track;
  onReviewed: (updatedTrack: Track) => void;
  onNavigateTrack: (direction: 1 | -1) => void;
  /** Bumped whenever the class editor changes something. Classes are
   *  editable now, so a rename has to reach this panel while it is
   *  open rather than on the next remount. */
  classesVersion?: number;
}

function TrackReview({ project, track, onReviewed, onNavigateTrack, classesVersion = 0 }: Props) {
  const [timeline, setTimeline] = useState<TrackTimeline | null>(null);
  const [classSchema, setClassSchema] = useState<ProjectClass[]>([]);
  const [frameIndex, setFrameIndex] = useState(0);
  const [classId, setClassId] = useState<number | "">("");
  const [bbox, setBbox] = useState<Bbox>([0, 0, 0, 0]);
  const [rendered, setRendered] = useState({ width: 0, height: 0 });
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [ocrCandidates, setOcrCandidates] = useState<OcrCandidate[]>([]);
  const [ocrRunning, setOcrRunning] = useState(false);
  const [correctionText, setCorrectionText] = useState("");
  /** The plate currently on the annotation, as the server has it. */
  const [plateText, setPlateText] = useState("");
  /** Whether this track has a label yet. A plate is an attribute of one,
   *  so there is nowhere to put a reading until the track is reviewed. */
  const [hasLabel, setHasLabel] = useState(false);
  const imgRef = useRef<HTMLImageElement>(null);

  useEffect(() => {
    api.getClassSchema(project.id).then(setClassSchema).catch((e) => setError(String(e)));
  }, [project.id, classesVersion]);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setMessage(null);
    Promise.all([api.getTrackTimeline(track.id), api.getAnnotation(track.id)])
      .then(([tl, annotation]) => {
        if (cancelled) return;
        setTimeline(tl);
        // The plate lives on the annotation now, so it arrives with it.
        const existing = (annotation?.attributes?.plate_text as string | undefined) ?? "";
        setPlateText(existing);
        setCorrectionText(existing);
        setHasLabel(annotation !== null);
        let initialIndex = tl.frames.findIndex((f) => f.flags_json?.roles.includes("best_detection"));
        if (initialIndex < 0) initialIndex = 0;
        if (annotation) {
          const annotatedIndex = tl.frames.findIndex((f) => f.id === annotation.frame_candidate_id);
          if (annotatedIndex >= 0) initialIndex = annotatedIndex;
          setClassId(annotation.class_id ?? "");
          setBbox(annotation.bbox_json);
        } else if (tl.frames.length > 0) {
          setBbox(tl.frames[initialIndex].bbox_json);
        }
        setFrameIndex(initialIndex);
      })
      .catch((e) => !cancelled && setError(String(e)));
    api
      .listOcrCandidates(track.id)
      .then((c) => !cancelled && setOcrCandidates(c))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [track.id]);

  async function handleRunOcr() {
    setOcrRunning(true);
    setError(null);
    try {
      await api.runOcr(track.id);
      setOcrCandidates(await api.listOcrCandidates(track.id));
    } catch (e) {
      setError(String(e));
    } finally {
      setOcrRunning(false);
    }
  }

  /** Take one of the model's readings as the plate. Clicking a
   *  candidate and typing the same characters are the same act, so
   *  both end up in the same place - the annotation. */
  function handleUseReading(text: string) {
    setCorrectionText(text);
    void savePlateText(text);
  }

  async function savePlateText(text: string) {
    try {
      // What comes back, not what was typed. The server canonicalises -
      // "mh 12 ab 1234" is stored as MH12AB1234 and "!!!" clears the
      // field entirely - so echoing the input reported a save that did
      // not happen, and in the "!!!" case announced a plate it had just
      // wiped.
      const saved = await api.setTrackPlateText(track.id, text);
      const stored = (saved.attributes?.plate_text as string | undefined) ?? "";
      setPlateText(stored);
      setCorrectionText(stored);
      setError(null);
      setMessage(stored ? `Plate saved: ${stored}` : "Plate cleared.");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  const frames = timeline?.frames ?? [];
  const currentFrame: FrameCandidate | undefined = frames[frameIndex];

  function selectFrame(index: number) {
    if (index < 0 || index >= frames.length) return;
    setFrameIndex(index);
    setBbox(frames[index].bbox_json);
  }

  async function submit(decision: "accepted" | "hard" | "failed") {
    if (!currentFrame) return;
    if (decision !== "failed" && classId === "") {
      setError("Pick a class before accepting or marking hard.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const result = await api.submitReview(track.id, {
        frame_candidate_id: currentFrame.id,
        decision,
        class_id: classId === "" ? null : classId,
        bbox_json: bbox,
      });
      setMessage(`Saved as ${decision}.`);
      // Every decision creates the label a plate hangs off, including
      // "failed" - the review writes a human annotation whatever it
      // decided. Excluding failed here left the field disabled, saying
      // "review this track first", on a track that had just been
      // reviewed and whose plate the server would have accepted.
      setHasLabel(true);
      onReviewed(result.track);
      onNavigateTrack(1);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (isEditableTarget(e.target)) return;
      switch (e.key) {
        case "ArrowLeft":
          selectFrame(frameIndex - 1);
          break;
        case "ArrowRight":
          selectFrame(frameIndex + 1);
          break;
        case "a":
        case "A":
          submit("accepted");
          break;
        case "h":
        case "H":
          submit("hard");
          break;
        case "f":
        case "F":
          submit("failed");
          break;
        case "[":
          onNavigateTrack(-1);
          break;
        case "]":
          onNavigateTrack(1);
          break;
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  });

  if (error && !timeline)
    return (
      <p className="error" style={{ padding: "1.5rem" }}>
        <IconAlert /> {error}
      </p>
    );
  if (!timeline) return <p className="placeholder">Loading track…</p>;
  if (!currentFrame) return <p className="placeholder">This track has no frame candidates.</p>;

  // The saved image is already cropped to the frame's own detected bbox
  // (see docs/HANDOFF.md Phase 4 note), so the overlay maps the edited
  // bbox back into the crop's local pixel space using that frame's
  // original bbox as the origin - not the full source frame.
  const originalBbox = currentFrame.bbox_json;
  const originalWidth = Math.max(1, originalBbox[2] - originalBbox[0]);
  const originalHeight = Math.max(1, originalBbox[3] - originalBbox[1]);
  const scaleX = rendered.width / originalWidth;
  const scaleY = rendered.height / originalHeight;
  const overlay = {
    x: (bbox[0] - originalBbox[0]) * scaleX,
    y: (bbox[1] - originalBbox[1]) * scaleY,
    width: Math.max(0, bbox[2] - bbox[0]) * scaleX,
    height: Math.max(0, bbox[3] - bbox[1]) * scaleY,
  };

  return (
    <div className="track-review">
      <div className="review-header">
        <h2>
          Track {track.tracker_track_id}
          {track.bucket && <span className={`badge bucket-${track.bucket}`}>{track.bucket}</span>}
        </h2>
        <span className="hint">
          <span className="kbd">&larr;</span>/<span className="kbd">&rarr;</span> frame
          <span className="kbd">A</span> accept
          <span className="kbd">H</span> hard
          <span className="kbd">F</span> failed
          <span className="kbd">[</span>/<span className="kbd">]</span> prev/next track
        </span>
      </div>

      <div className="review-body">
        <div className="frame-viewer">
          <div className="frame-image-wrap">
            <img
              ref={imgRef}
              src={api.frameImageUrl(currentFrame.id)}
              alt={`Frame ${currentFrame.frame_index}`}
              onLoad={(e) => {
                const el = e.currentTarget;
                setRendered({ width: el.clientWidth, height: el.clientHeight });
              }}
            />
            <svg className="bbox-overlay">
              <rect x={overlay.x} y={overlay.y} width={overlay.width} height={overlay.height} />
            </svg>
          </div>
          <p className="frame-meta">
            frame {currentFrame.frame_index} @ {currentFrame.timestamp_ms}ms &middot; {currentFrame.detector_class} (
            {(currentFrame.detector_confidence * 100).toFixed(0)}%)
            {currentFrame.flags_json?.truncated && <span className="flag">truncated</span>}
          </p>

          <div className="filmstrip">
            {frames.map((f, i) => (
              <button
                key={f.id}
                className={i === frameIndex ? "selected" : ""}
                onClick={() => selectFrame(i)}
                title={`frame ${f.frame_index}`}
              >
                <img src={api.frameImageUrl(f.id)} alt="" />
                {f.flags_json?.roles.includes("best_detection") && <span className="star">&#9733;</span>}
              </button>
            ))}
          </div>
        </div>

        <div className="review-controls">
          <div className="card">
            <label>
              Class
              <select value={classId} onChange={(e) => setClassId(e.target.value === "" ? "" : Number(e.target.value))}>
                <option value="">(none)</option>
                {classSchema.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>

            <fieldset className="bbox-editor">
              <legend>Bounding box (source-frame pixels)</legend>
              {(["x1", "y1", "x2", "y2"] as const).map((label, i) => (
                <label key={label}>
                  {label}
                  <input
                    type="number"
                    value={bbox[i]}
                    onChange={(e) => {
                      const next = [...bbox] as Bbox;
                      next[i] = Number(e.target.value);
                      setBbox(next);
                    }}
                  />
                </label>
              ))}
            </fieldset>
          </div>

          <div className="review-actions">
            <button className="btn-primary" disabled={saving} onClick={() => submit("accepted")}>
              <IconCheck /> Accept <span className="kbd">A</span>
            </button>
            <button className="btn-ghost" disabled={saving} onClick={() => submit("hard")}>
              <IconAlert /> Hard <span className="kbd">H</span>
            </button>
            <button className="btn-danger-ghost" disabled={saving} onClick={() => submit("failed")}>
              <IconX /> Failed <span className="kbd">F</span>
            </button>
          </div>

          <p className="review-status-line">Current status: {track.review_status}</p>
          {error && (
            <p className="error">
              <IconAlert /> {error}
            </p>
          )}
          {message && (
            <p className="status">
              <IconCheck /> {message}
            </p>
          )}

          <div className="ocr-panel card">
            <div className="ocr-panel-header">
              <h4>Plate OCR</h4>
              <button disabled={ocrRunning} onClick={handleRunOcr}>
                {ocrRunning ? "Running…" : "Run OCR"}
              </button>
            </div>
            <ul className="ocr-candidate-list">
              {ocrCandidates.map((c) => (
                <li key={c.id} className={c.selected ? "selected" : ""}>
                  <span className="ocr-text">{c.normalized_text || "(no text)"}</span>
                  <span className="ocr-meta">{(c.confidence * 100).toFixed(0)}% confident</span>
                  <button disabled={!hasLabel} onClick={() => handleUseReading(c.normalized_text)}>
                    Use
                  </button>
                  {c.selected && (
                    <span className="badge badge-success">
                      <IconCheck /> best
                    </span>
                  )}
                </li>
              ))}
              {ocrCandidates.length === 0 && <li className="empty">No OCR attempts yet.</li>}
            </ul>
            <div className="ocr-correction">
              {/* Disabled rather than refused on save. A plate belongs to
                  a label and there is none yet, and the old behaviour -
                  accept the typing, then 409 with "review it first" -
                  sent the user to a control that navigates to the next
                  track, losing what they had just read off the image. */}
              <input
                type="text"
                aria-label="Plate text"
                placeholder={hasLabel ? "e.g. MH12AB1234" : "Review this track first"}
                value={correctionText}
                disabled={!hasLabel}
                onChange={(e) => setCorrectionText(e.target.value)}
              />
              <button disabled={!hasLabel} onClick={() => savePlateText(correctionText)}>
                Save
              </button>
            </div>
            {!hasLabel && (
              <p className="ocr-recorded">Accept or flag this track to record a plate against it.</p>
            )}
            {plateText && <p className="ocr-recorded">Recorded on this label: {plateText}</p>}
          </div>
        </div>
      </div>
    </div>
  );
}

export default TrackReview;
