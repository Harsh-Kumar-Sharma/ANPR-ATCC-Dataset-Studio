import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { IconAlert, IconBroadcast, IconX } from "../Icons";
import type { RtspSessionStatus } from "../types";

interface Props {
  runId: string;
  onClose: () => void;
}

// Each poll waits for the previous frame to arrive before scheduling the
// next, so a slow backend is never flooded with overlapping requests.
const POLL_DELAY_MS = 100;
const FPS_WINDOW_MS = 2000;

function LivePreview({ runId, onClose }: Props) {
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [status, setStatus] = useState<RtspSessionStatus | null>(null);
  const [fps, setFps] = useState(0);
  const objectUrlRef = useRef<string | null>(null);
  const stoppedRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let lastSequence: string | null = null;
    const arrivals: number[] = [];

    async function poll() {
      try {
        const response = await fetch(api.rtspPreviewUrl(runId), { cache: "no-store" });
        if (response.status === 200) {
          const sequence = response.headers.get("X-Frame-Sequence");
          const blob = await response.blob();
          if (!cancelled && (sequence === null || sequence !== lastSequence)) {
            lastSequence = sequence;
            // Swapping in an already-downloaded blob avoids the flicker a
            // plain <img src> change shows while the next image loads.
            const url = URL.createObjectURL(blob);
            if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
            objectUrlRef.current = url;
            setImageUrl(url);

            const now = performance.now();
            arrivals.push(now);
            while (arrivals.length > 0 && now - arrivals[0] > FPS_WINDOW_MS) arrivals.shift();
            setFps(arrivals.length / (FPS_WINDOW_MS / 1000));
          }
        }
      } catch {
        // Backend momentarily unreachable (e.g. a dev reload) - keep polling.
      }
      if (!cancelled && !stoppedRef.current) timer = setTimeout(poll, POLL_DELAY_MS);
    }

    stoppedRef.current = false;
    poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current);
        objectUrlRef.current = null;
      }
    };
  }, [runId]);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      api
        .getRtspStatus(runId)
        .then((s) => {
          if (cancelled) return;
          setStatus(s);
          stoppedRef.current = s.stopped;
        })
        .catch(() => undefined);
    load();
    const interval = setInterval(load, 1000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [runId]);

  const live = status === null || !status.stopped;
  const waitingMessage = status?.error
    ? "The session ended before any frame was processed."
    : status && !status.connected
      ? status.reconnect_attempts > 0
        ? `Reconnecting to the camera (attempt ${status.reconnect_attempts})…`
        : "Connecting to the camera…"
      : "Waiting for the first processed frame…";

  return (
    <div className="live-preview">
      <div className="player-header">
        <h2>
          <IconBroadcast /> Live preview
        </h2>
        <button className="btn-ghost" onClick={onClose} title="Close preview">
          <IconX />
        </button>
      </div>

      <div className="live-stage">
        {imageUrl ? (
          <img src={imageUrl} alt="Latest processed camera frame with detections" />
        ) : (
          <p className="live-waiting">{waitingMessage}</p>
        )}
        <span className={`live-badge${live ? "" : " ended"}`}>
          <span className="dot" />
          {live ? "LIVE" : "ENDED"}
          {live && imageUrl ? ` · ${fps.toFixed(1)} fps` : ""}
        </span>
      </div>

      {status?.error && (
        <p className="error">
          <IconAlert /> {status.error}
        </p>
      )}

      {status && (
        <div className="player-controls">
          <span className="player-stat">Frames captured {status.frames_captured}</span>
          <span className={`player-stat${status.frames_dropped > 0 ? " warn" : ""}`}>
            Dropped {status.frames_dropped}
          </span>
          <span className="player-stat">Tracks saved {status.tracks_persisted}</span>
        </div>
      )}

      <p className="player-hint">
        Boxes are drawn when each frame is processed. Dropped frames mean detection is slower than the camera - the
        preview shows the newest frame it managed to process. Vehicles are saved as reviewable tracks about every 10
        seconds and when you stop the session.
      </p>
    </div>
  );
}

export default LivePreview;
