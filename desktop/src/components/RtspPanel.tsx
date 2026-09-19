import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import ModelPicker, { useModelChoice } from "./ModelPicker";
import { IconAlert, IconBroadcast, IconCheck } from "../Icons";
import type { Project, RtspSessionStatus } from "../types";

interface Props {
  project: Project;
  onSessionEnded: () => void;
  onShowPreview: (runId: string) => void;
}

function RtspPanel({ project, onSessionEnded, onShowPreview }: Props) {
  const [url, setUrl] = useState("");
  const [expectedFps, setExpectedFps] = useState(10);
  // The same choice as offline detection: a live stream is where a
  // model you trained yourself earns its keep.
  const modelChoice = useModelChoice(project.id);
  const [runId, setRunId] = useState<string | null>(null);
  const [status, setStatus] = useState<RtspSessionStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
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
    setStarting(true);
    try {
      const result = await api.startRtspSession(project.id, url.trim(), expectedFps, modelChoice.modelId);
      setRunId(result.run.id);
      onShowPreview(result.run.id);
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
    } finally {
      setStarting(false);
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
  const dotClass = status?.connected ? "connected" : status && status.reconnect_attempts > 0 ? "reconnecting" : "disconnected";
  const statusLabel = status?.connected
    ? "Connected"
    : status && status.reconnect_attempts > 0
      ? `Reconnecting (attempt ${status.reconnect_attempts})…`
      : "Disconnected";

  return (
    <div className="rtsp-panel">
      <div className="section-title">
        <IconBroadcast /> Live RTSP
      </div>

      {!isActive && (
        <form onSubmit={handleStart} className="rtsp-start-form">
          <input
            type="text"
            placeholder="rtsp://camera/stream"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
          <label className="fps-control">
            Expected FPS
            <input
              type="number"
              min={1}
              value={expectedFps}
              onChange={(e) => setExpectedFps(Number(e.target.value))}
            />
          </label>
          <ModelPicker choice={modelChoice} id="live-model" disabled={starting} />
          <button type="submit" className="btn-primary btn-block" disabled={!url.trim() || starting}>
            {starting ? "Starting…" : "Start Live Capture"}
          </button>
        </form>
      )}

      {error && (
        <p className="error" style={{ marginTop: "0.6rem" }}>
          <IconAlert /> {error}
        </p>
      )}

      {status && (
        <div className="card rtsp-status" style={{ marginTop: "0.8rem" }}>
          <span className="rtsp-status-line">
            <span className={`status-dot ${dotClass}`} />
            {statusLabel}
          </span>
          {status.error && (
            <p className="error">
              <IconAlert /> {status.error}
            </p>
          )}
          <div className="stat-row">
            <span>Frames captured</span>
            <strong>{status.frames_captured}</strong>
          </div>
          <div className="stat-row">
            <span>Dropped</span>
            <strong>{status.frames_dropped}</strong>
          </div>
          <div className="stat-row">
            <span>Tracks persisted</span>
            <strong>{status.tracks_persisted}</strong>
          </div>
          {runId && (
            <button className="btn-block" onClick={() => onShowPreview(runId)}>
              <IconBroadcast /> Show live preview
            </button>
          )}
          {isActive && (
            <button className="btn-danger-ghost btn-block" onClick={handleStop}>
              Stop
            </button>
          )}
          {status.stopped && (
            <p className="status">
              <IconCheck /> Session ended.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

export default RtspPanel;
