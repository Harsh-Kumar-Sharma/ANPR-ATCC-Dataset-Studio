import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { IconAlert, IconFilm, IconX } from "../Icons";
import type { DetectionFrame, Project, Source, SourceDetections, Track } from "../types";

interface Props {
  project: Project;
  source: Source;
  tracks: Track[];
  onSelectTrack: (track: Track) => void;
  onClose: () => void;
}

interface TrackSpan {
  trackId: string;
  trackerTrackId: number;
  startMs: number;
  endMs: number;
  bucket: string | null;
}

type FrameCallbackVideo = HTMLVideoElement & {
  requestVideoFrameCallback?: (callback: (now: number, metadata: { mediaTime: number }) => void) => number;
  cancelVideoFrameCallback?: (handle: number) => void;
};

const BUCKET_COLOR: Record<string, string> = {
  BEST_DETECTION: "#22c55e",
  HARD: "#f59e0b",
  FAILED: "#ef4444",
};
const DEFAULT_COLOR = "#38bdf8";
const SPEEDS = [0.25, 0.5, 1, 2];
// Seek a little before a vehicle appears, so it can be seen entering.
const PRE_ROLL_MS = 600;

function colorFor(bucket: string | null): string {
  return BUCKET_COLOR[bucket ?? ""] ?? DEFAULT_COLOR;
}

// Detections exist only on sampled frames (e.g. 5 per second), so each
// box is shown for the nearest sample. Holding it for a bit over half the
// typical gap keeps it continuous between samples, without letting it
// linger long after its vehicle's track has ended.
function holdWindowMs(frames: DetectionFrame[]): number {
  const gaps = frames
    .slice(1)
    .map((frame, i) => frame.timestamp_ms - frames[i].timestamp_ms)
    .filter((gap) => gap > 0)
    .sort((a, b) => a - b);
  if (gaps.length === 0) return 250;
  const median = gaps[Math.floor(gaps.length / 2)];
  return Math.min(600, Math.max(80, median * 0.6));
}

function nearestFrame(frames: DetectionFrame[], timeMs: number, holdMs: number): DetectionFrame | null {
  if (frames.length === 0) return null;
  let lo = 0;
  let hi = frames.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (frames[mid].timestamp_ms < timeMs) lo = mid + 1;
    else hi = mid;
  }
  let best: DetectionFrame | null = null;
  for (const candidate of [frames[lo], frames[lo - 1]]) {
    if (!candidate) continue;
    const distance = Math.abs(candidate.timestamp_ms - timeMs);
    if (distance <= holdMs && (best === null || distance < Math.abs(best.timestamp_ms - timeMs))) best = candidate;
  }
  return best;
}

function formatClock(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "0:00.0";
  const totalSeconds = ms / 1000;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds - minutes * 60;
  return `${minutes}:${seconds.toFixed(1).padStart(4, "0")}`;
}

// The backend stores naive UTC timestamps; without a zone suffix the
// browser would read them as local time.
function parseUtc(iso: string): Date {
  return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
}

function VideoPlayer({ project, source, tracks, onSelectTrack, onClose }: Props) {
  const videoRef = useRef<FrameCallbackVideo>(null);
  const [detections, setDetections] = useState<SourceDetections | null>(null);
  const [runId, setRunId] = useState<string | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [videoError, setVideoError] = useState(false);
  const [timeMs, setTimeMs] = useState(0);
  const [durationMs, setDurationMs] = useState(source.duration_ms);
  const [videoSize, setVideoSize] = useState<{ width: number; height: number } | null>(null);
  const [showBoxes, setShowBoxes] = useState(true);
  const [speed, setSpeed] = useState(1);

  // Re-fetch when the project's tracks change, so boxes pick up a run that
  // just finished and review statuses changed elsewhere.
  useEffect(() => {
    let cancelled = false;
    api
      .getSourceDetections(project.id, source.id, runId)
      .then((result) => {
        if (!cancelled) {
          setDetections(result);
          setError(null);
        }
      })
      .catch((e) => !cancelled && setError(String(e)));
    return () => {
      cancelled = true;
    };
  }, [project.id, source.id, runId, tracks]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    let stopped = false;
    let frameHandle = 0;
    let animationHandle = 0;
    const syncFromCurrentTime = () => setTimeMs(video.currentTime * 1000);

    // requestVideoFrameCallback reports the media time of the frame that
    // was actually painted, so boxes line up with the picture exactly;
    // currentTime polling can be a frame or two off.
    if (video.requestVideoFrameCallback) {
      const onFrame = (_now: number, metadata: { mediaTime: number }) => {
        if (stopped) return;
        setTimeMs(metadata.mediaTime * 1000);
        frameHandle = video.requestVideoFrameCallback!(onFrame);
      };
      frameHandle = video.requestVideoFrameCallback(onFrame);
    } else {
      const loop = () => {
        if (stopped) return;
        syncFromCurrentTime();
        animationHandle = requestAnimationFrame(loop);
      };
      animationHandle = requestAnimationFrame(loop);
    }

    video.addEventListener("seeked", syncFromCurrentTime);
    video.addEventListener("timeupdate", syncFromCurrentTime);
    return () => {
      stopped = true;
      if (frameHandle && video.cancelVideoFrameCallback) video.cancelVideoFrameCallback(frameHandle);
      cancelAnimationFrame(animationHandle);
      video.removeEventListener("seeked", syncFromCurrentTime);
      video.removeEventListener("timeupdate", syncFromCurrentTime);
    };
  }, []);

  useEffect(() => {
    if (videoRef.current) videoRef.current.playbackRate = speed;
  }, [speed]);

  const frames = useMemo(() => detections?.frames ?? [], [detections]);
  const holdMs = useMemo(() => holdWindowMs(frames), [frames]);
  const current = nearestFrame(frames, timeMs, holdMs);

  const spans = useMemo(() => {
    const byTrack = new Map<string, TrackSpan>();
    for (const frame of frames) {
      for (const box of frame.boxes) {
        const span = byTrack.get(box.track_id);
        if (span) {
          span.startMs = Math.min(span.startMs, frame.timestamp_ms);
          span.endMs = Math.max(span.endMs, frame.timestamp_ms);
        } else {
          byTrack.set(box.track_id, {
            trackId: box.track_id,
            trackerTrackId: box.tracker_track_id,
            startMs: frame.timestamp_ms,
            endMs: frame.timestamp_ms,
            bucket: box.bucket,
          });
        }
      }
    }
    return [...byTrack.values()].sort((a, b) => a.startMs - b.startMs);
  }, [frames]);

  const frameWidth = detections?.width || videoSize?.width || 0;
  const frameHeight = detections?.height || videoSize?.height || 0;
  const aspectMismatch =
    videoSize !== null &&
    frameWidth > 0 &&
    frameHeight > 0 &&
    Math.abs(frameWidth / frameHeight - videoSize.width / videoSize.height) > 0.02;

  function seek(ms: number) {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = Math.max(0, Math.min(ms, durationMs)) / 1000;
    setTimeMs(video.currentTime * 1000);
  }

  function play() {
    videoRef.current?.play().catch(() => undefined);
  }

  function jumpToVehicle(direction: 1 | -1) {
    const target =
      direction === 1
        ? spans.find((span) => span.startMs - PRE_ROLL_MS > timeMs + 100)
        : [...spans].reverse().find((span) => span.startMs - PRE_ROLL_MS < timeMs - 300);
    if (!target) return;
    seek(target.startMs - PRE_ROLL_MS);
    play();
  }

  function percent(ms: number): number {
    if (durationMs <= 0) return 0;
    return Math.max(0, Math.min(100, (ms / durationMs) * 100));
  }

  const stageRef = useRef<HTMLDivElement>(null);
  const [stageSize, setStageSize] = useState<{ width: number; height: number } | null>(null);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const observer = new ResizeObserver(([entry]) =>
      setStageSize({ width: entry.contentRect.width, height: entry.contentRect.height }),
    );
    observer.observe(stage);
    return () => observer.disconnect();
  }, []);

  const fileName = source.path_or_uri.split(/[\\/]/).pop();
  // Labels are sized in screen pixels and converted into viewBox units. The
  // SVG is scaled down with the displayed video, so a size given in video
  // pixels becomes unreadable on a small player (~8px at 634px wide).
  const labelScreenPx = 13;
  const renderScale =
    stageSize && frameWidth > 0 && frameHeight > 0
      ? Math.min(stageSize.width / frameWidth, stageSize.height / frameHeight)
      : 0;
  const fontSize = renderScale > 0 ? labelScreenPx / renderScale : Math.max(12, frameHeight * 0.024);
  const noRunYet = detections !== null && detections.run_id === null;

  return (
    <div className="video-player">
      <div className="player-header">
        <h2 title={source.path_or_uri}>
          <IconFilm /> {fileName}
        </h2>
        {detections && detections.runs.length > 1 && (
          <select
            value={detections.run_id ?? ""}
            onChange={(e) => setRunId(e.target.value)}
            title="Which processing run's detections to draw"
          >
            {detections.runs.map((run, i) => (
              <option key={run.id} value={run.id}>
                {i === 0 ? "Latest" : "Earlier"} ·{" "}
                {parseUtc(run.started_at).toLocaleString(undefined, {
                  month: "short",
                  day: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                })}{" "}
                · {run.track_count} tracks
              </option>
            ))}
          </select>
        )}
        <label className="toggle">
          <input type="checkbox" checked={showBoxes} onChange={(e) => setShowBoxes(e.target.checked)} />
          Boxes
        </label>
        <button className="btn-ghost" onClick={onClose} title="Close player">
          <IconX />
        </button>
      </div>

      <div className="player-stage" ref={stageRef}>
        <video
          ref={videoRef}
          src={api.sourceVideoUrl(project.id, source.id)}
          controls
          preload="metadata"
          onLoadedMetadata={(e) => {
            const video = e.currentTarget;
            if (Number.isFinite(video.duration)) setDurationMs(video.duration * 1000);
            setVideoSize({ width: video.videoWidth, height: video.videoHeight });
            video.playbackRate = speed;
          }}
          onError={() => setVideoError(true)}
        />
        {showBoxes && current && frameWidth > 0 && !videoError && (
          <svg
            className="player-overlay"
            viewBox={`0 0 ${frameWidth} ${frameHeight}`}
            preserveAspectRatio="xMidYMid meet"
          >
            {current.boxes.map((box) => {
              const [x1, y1, x2, y2] = box.bbox;
              const color = colorFor(box.bucket);
              const track = tracks.find((t) => t.id === box.track_id);
              const label = `#${box.tracker_track_id} ${box.detector_class} ${Math.round(box.confidence * 100)}%`;
              return (
                <g key={box.track_id} className="det-box" onClick={() => track && onSelectTrack(track)}>
                  <title>{track ? `${label} - click to review this track` : label}</title>
                  <rect
                    className="det-rect"
                    x={x1}
                    y={y1}
                    width={Math.max(0, x2 - x1)}
                    height={Math.max(0, y2 - y1)}
                    stroke={color}
                  />
                  <text
                    className="det-label"
                    x={x1}
                    y={Math.max(fontSize, y1 - fontSize * 0.3)}
                    fontSize={fontSize}
                    fill={color}
                    strokeWidth={fontSize * 0.2}
                  >
                    {label}
                  </text>
                </g>
              );
            })}
          </svg>
        )}
        {videoError && (
          <div className="player-stage-message">
            <IconAlert />
            <p>
              This video can't be played inside the app - the embedded browser doesn't support its codec or
              container. Its detections are still available in the Tracks list.
            </p>
          </div>
        )}
      </div>

      {error && (
        <p className="error">
          <IconAlert /> {error}
        </p>
      )}
      {aspectMismatch && (
        <p className="warning">
          <IconAlert /> The decoded video is {videoSize!.width}x{videoSize!.height} but detections were recorded at{" "}
          {frameWidth}x{frameHeight} (often a rotated phone video). Boxes may not line up.
        </p>
      )}

      {noRunYet ? (
        <p className="player-hint">
          No detections for this video yet. Run <strong>Detect + Track</strong> in the Sources tab - boxes appear here
          once it finishes.
        </p>
      ) : (
        <>
          <div
            className="player-timeline"
            title="Click to seek. Coloured marks are where tracked vehicles appear."
            onClick={(e) => {
              const rect = e.currentTarget.getBoundingClientRect();
              seek(((e.clientX - rect.left) / rect.width) * durationMs);
            }}
          >
            {spans.map((span) => (
              <div
                key={span.trackId}
                className="tl-marker"
                style={{
                  left: `${percent(span.startMs)}%`,
                  width: `${Math.max(0.35, percent(span.endMs) - percent(span.startMs))}%`,
                  background: colorFor(span.bucket),
                }}
                title={`Vehicle #${span.trackerTrackId} at ${formatClock(span.startMs)}`}
                onClick={(e) => {
                  e.stopPropagation();
                  seek(span.startMs - PRE_ROLL_MS);
                  play();
                }}
              />
            ))}
            <div className="tl-playhead" style={{ left: `${percent(timeMs)}%` }} />
          </div>

          <div className="player-controls">
            <button onClick={() => jumpToVehicle(-1)} disabled={spans.length === 0}>
              ‹ Prev vehicle
            </button>
            <button onClick={() => jumpToVehicle(1)} disabled={spans.length === 0}>
              Next vehicle ›
            </button>
            <label className="toggle">
              Speed
              <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
                {SPEEDS.map((s) => (
                  <option key={s} value={s}>
                    {s}x
                  </option>
                ))}
              </select>
            </label>
            <span className="player-stat">
              {current ? `${current.boxes.length} vehicle(s) on screen` : "No vehicle at this moment"}
            </span>
            <span className="player-stat muted">
              {spans.length} tracked vehicle(s) · {formatClock(timeMs)} / {formatClock(durationMs)}
            </span>
          </div>

          <div className="player-legend">
            <span>
              <i className="legend-swatch" style={{ background: BUCKET_COLOR.BEST_DETECTION }} /> Best
            </span>
            <span>
              <i className="legend-swatch" style={{ background: BUCKET_COLOR.HARD }} /> Hard
            </span>
            <span>
              <i className="legend-swatch" style={{ background: BUCKET_COLOR.FAILED }} /> Failed
            </span>
            <span className="muted">Click a box to review that track.</span>
          </div>
        </>
      )}
    </div>
  );
}

export default VideoPlayer;
