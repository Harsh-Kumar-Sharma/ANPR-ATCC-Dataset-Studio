import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Project, RtspSessionStatus } from "../types";

interface Props {
  project: Project;
  onSessionEnded: () => void;
}

function RtspPanel({ project, onSessionEnded }: Props) {
  const [url, setUrl] = useState("");
  const [expectedFps, setExpectedFps] = useState(10);
  const [runId, setRunId] = useState<string | null>(null);
  const [status, setStatus] = useState<RtspSessionStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  async function handleStart(e: React.FormEvent) {
    e.preventDefault();
    if (!url.trim()) return;
    setError(null);
    try {
      const result = await api.startRtspSession(project.id, url.trim(), expectedFps);
      setRunId(result.run.id);
      pollRef.current = setInterval(async () => {
        try {
          const s = await api.getRtspStatus(result.run.id);
          setStatus(s);
          if (s.stopped && pollRef.current) {
            clearInterval(pollRef.current);
            onSessionEnded();
          }
        } catch (e) {
          setError(String(e));
        }
      }, 1000);
    } catch (e) {
      setError(String(e));
    }
  }

  async function handleStop() {
    if (!runId) return;
    try {
      await api.stopRtspSession(runId);
    } catch (e) {
      setError(String(e));
    }
  }

  const isActive = status !== null && !status.stopped;

  return (
    <div className="rtsp-panel">
      <h3>Live RTSP</h3>
      {!isActive && (
        <form onSubmit={handleStart} className="rtsp-start-form">
          <input
            type="text"
            placeholder="rtsp://camera/stream"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
          <input
            type="number"
            min={1}
            title="expected FPS"
            value={expectedFps}
            onChange={(e) => setExpectedFps(Number(e.target.value))}
          />
          <button type="submit">Start Live Capture</button>
        </form>
      )}
      {error && <p className="error">{error}</p>}
      {status && (
        <div className="rtsp-status">
          <p>
            {status.connected ? "connected" : status.reconnect_attempts > 0 ? "reconnecting..." : "disconnected"}
            {status.error && <span className="error"> - {status.error}</span>}
          </p>
          <p>
            frames: {status.frames_captured} (dropped {status.frames_dropped}) - tracks: {status.tracks_persisted}
          </p>
          {isActive && <button onClick={handleStop}>Stop</button>}
          {status.stopped && <p className="status">Session ended.</p>}
        </div>
      )}
    </div>
  );
}

export default RtspPanel;
