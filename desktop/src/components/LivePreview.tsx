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
/** Long enough that a one-frame wobble does not move the number. */
const RATE_WINDOW_MS = 5000;

function LivePreview({ runId, onClose }: Props) {
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [status, setStatus] = useState<RtspSessionStatus | null>(null);
  // How fast frames are actually arriving from the camera, measured
  // from the session's own counter. The rate the preview refreshes at
  // is not this number and never was: the backend throttles rendering
  // to about 8 a second so it is not JPEG-encoding every frame, and
  // the poll here adds its own ceiling. Showing that as "fps" next to
  // LIVE read as throughput, which is how "I asked for 30 and it says
  // 6.5" happened.
  const [cameraFps, setCameraFps] = useState<number | null>(null);
  const samples = useRef<{ at: number; captured: number }[]>([]);
  const objectUrlRef = useRef<string | null>(null);
  const stoppedRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let lastSequence: string | null = null;

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

          const now = performance.now();
          samples.current.push({ at: now, captured: s.frames_captured });
          while (samples.current.length > 2 && now - samples.current[0].at > RATE_WINDOW_MS) {
            samples.current.shift();
          }
          const first = samples.current[0];
          const seconds = (now - first.at) / 1000;
          // One sample says nothing, and a window shorter than a
          // second turns counter jitter into a wild number.
          if (seconds >= 1) setCameraFps((s.frames_captured - first.captured) / seconds);
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
          {live && cameraFps !== null ? ` · ${cameraFps.toFixed(1)} fps from camera` : ""}
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
        The rate beside LIVE is how fast frames are arriving from the camera, which is the camera and the network's decision - not a setting. The preview itself refreshes more slowly than that on purpose. Boxes are drawn when each frame is processed. Dropped frames mean detection is slower than the camera - the
        preview shows the newest frame it managed to process. Vehicles are saved as reviewable tracks about every 10
        seconds and when you stop the session.
      </p>
    </div>
  );
}

export default LivePreview;
