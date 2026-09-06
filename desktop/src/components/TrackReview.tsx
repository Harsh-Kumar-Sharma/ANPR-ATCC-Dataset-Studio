import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { AtccClass, FrameCandidate, OcrCandidate, Project, Track, TrackTimeline } from "../types";

interface Props {
  project: Project;
  track: Track;
  onReviewed: (updatedTrack: Track) => void;
  onNavigateTrack: (direction: 1 | -1) => void;
}

type Bbox = [number, number, number, number];

function isEditableTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLInputElement || target instanceof HTMLSelectElement || target instanceof HTMLTextAreaElement;
}

function TrackReview({ project, track, onReviewed, onNavigateTrack }: Props) {
  const [timeline, setTimeline] = useState<TrackTimeline | null>(null);
  const [classSchema, setClassSchema] = useState<AtccClass[]>([]);
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
  const imgRef = useRef<HTMLImageElement>(null);

  useEffect(() => {
    api.getClassSchema(project.id).then(setClassSchema).catch((e) => setError(String(e)));
  }, [project.id]);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setMessage(null);
    Promise.all([api.getTrackTimeline(track.id), api.getAnnotation(track.id)])
      .then(([tl, annotation]) => {
        if (cancelled) return;
        setTimeline(tl);
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

  async function handleSelectOcr(candidateId: string) {
    try {
      await api.selectOcrCandidate(track.id, candidateId);
      setOcrCandidates(await api.listOcrCandidates(track.id));
    } catch (e) {
      setError(String(e));
    }
  }

  async function handleCorrectOcr() {
    if (!correctionText.trim() || !currentFrame) return;
    try {
      await api.correctOcr(track.id, correctionText.trim(), currentFrame.id);
      setCorrectionText("");
      setOcrCandidates(await api.listOcrCandidates(track.id));
    } catch (e) {
      setError(String(e));
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

  if (error && !timeline) return <p className="error">{error}</p>;
  if (!timeline) return <p>Loading track...</p>;
  if (!currentFrame) return <p>This track has no frame candidates.</p>;

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
          Track {track.tracker_track_id} - {track.bucket ?? "unclassified"}
        </h2>
        <span className="hint">
          Shortcuts: &larr;/&rarr; frame, A accept, H hard, F failed, [ / ] prev/next track
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
            frame {currentFrame.frame_index} @ {currentFrame.timestamp_ms}ms - {currentFrame.detector_class} (
            {(currentFrame.detector_confidence * 100).toFixed(0)}%)
            {currentFrame.flags_json?.truncated && <span className="flag"> truncated</span>}
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
                {f.flags_json?.roles.includes("best_detection") && <span className="star">*</span>}
              </button>
            ))}
          </div>
        </div>

        <div className="review-controls">
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

          <div className="review-actions">
            <button disabled={saving} onClick={() => submit("accepted")}>
              Accept (A)
            </button>
            <button disabled={saving} onClick={() => submit("hard")}>
              Mark Hard (H)
            </button>
            <button disabled={saving} onClick={() => submit("failed")}>
              Mark Failed (F)
            </button>
          </div>

          <p className="review-status-line">Current status: {track.review_status}</p>
          {error && <p className="error">{error}</p>}
          {message && <p className="status">{message}</p>}

          <div className="ocr-panel">
            <div className="ocr-panel-header">
              <h4>Plate OCR</h4>
              <button disabled={ocrRunning} onClick={handleRunOcr}>
                {ocrRunning ? "Running..." : "Run OCR"}
              </button>
            </div>
            <ul className="ocr-candidate-list">
              {ocrCandidates.map((c) => (
                <li key={c.id} className={c.selected ? "selected" : ""}>
                  <span className="ocr-text">{c.normalized_text || "(no text)"}</span>
                  <span className="ocr-meta">
                    {c.source} - {(c.confidence * 100).toFixed(0)}%
                  </span>
                  {!c.selected && <button onClick={() => handleSelectOcr(c.id)}>Select</button>}
                  {c.selected && <span className="badge">selected</span>}
                </li>
              ))}
              {ocrCandidates.length === 0 && <li className="empty">No OCR attempts yet.</li>}
            </ul>
            <div className="ocr-correction">
              <input
                type="text"
                placeholder="Correct plate text"
                value={correctionText}
                onChange={(e) => setCorrectionText(e.target.value)}
              />
              <button onClick={handleCorrectOcr}>Save correction</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default TrackReview;
