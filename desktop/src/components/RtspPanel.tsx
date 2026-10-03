import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import ModelPicker, { useModelChoice } from "./ModelPicker";
import { sourceLabel } from "../sourceLabel";
import { IconAlert, IconBroadcast, IconCheck } from "../Icons";
import type { LiveCamera, Project, RtspSessionStatus } from "../types";

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
  // Keep the frames themselves, not just what was detected in them.
  // A session that detects nothing otherwise leaves nothing to label,
  // which is exactly what happened: 2,453 frames, no tracks.
  const [keepFrames, setKeepFrames] = useState(false);
  const [keepEvery, setKeepEvery] = useState(10);
  // Frames kept of any one vehicle the model tracks: far, middle, near.
  const [perVehicle, setPerVehicle] = useState(3);
  // Run a small vehicle model too, and keep frames of vehicles the
  // plate model found no plate on.
  const [findMisses, setFindMisses] = useState(true);
  const [runId, setRunId] = useState<string | null>(null);
  const [status, setStatus] = useState<RtspSessionStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Cameras this project has watched before. Starting one again
  // should be a click, not four fields retyped - and Stop is the
  // most likely moment to want the same camera back.
  const [cameras, setCameras] = useState<LiveCamera[]>([]);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    api
      .listLiveCameras(project.id)
      .then((known) => {
        if (cancelled) return;
        setCameras(known);
        // The most recent one, filled in and ready. Only when the
        // field is untouched: overwriting a URL someone is halfway
        // through typing would be worse than not remembering at all.
        setUrl((current) => (current === "" && known[0] ? known[0].rtsp_url : current));
        if (known[0]) applySettings(known[0]);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
    // applySettings only reads setters, which are stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.id]);

  /** Put a remembered camera's settings back on screen. */
  function applySettings(camera: LiveCamera) {
    setExpectedFps(camera.expected_fps);
    setKeepFrames(camera.keep_frames);
    setKeepEvery(camera.keep_every);
    if (camera.model_id) modelChoice.choose(camera.model_id);
  }

  function useCamera(cameraId: string) {
    const camera = cameras.find((c) => c.id === cameraId);
    if (!camera) return;
    setUrl(camera.rtsp_url);
    applySettings(camera);
  }

  async function forgetCamera(camera: LiveCamera) {
    try {
      await api.forgetLiveCamera(project.id, camera.id);
      setCameras((previous) => previous.filter((c) => c.id !== camera.id));
    } catch (e) {
      setError(String(e));
    }
  }

  async function handleStart(e: React.FormEvent) {
    e.preventDefault();
    if (!url.trim()) return;
    setError(null);
    setStarting(true);
    try {
      const result = await api.startRtspSession(project.id, url.trim(), expectedFps, modelChoice.modelId, {
        keepFrames,
        every: keepEvery,
        perVehicle,
        findMisses,
      });
      // The backend remembers what was just started; re-read it so
      // the picker has the camera without waiting for a remount.
      api.listLiveCameras(project.id).then(setCameras).catch(() => undefined);
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
          {/* One remembered camera is already in the field below, so
              a list of one would be a menu with nothing to choose. */}
          {cameras.length > 1 && (
            <label className="saved-camera">
              <span>Camera</span>
              <select
                aria-label="Saved camera"
                value={cameras.find((c) => c.rtsp_url === url)?.id ?? ""}
                onChange={(e) => useCamera(e.target.value)}
              >
                <option value="">Type a new one below</option>
                {cameras.map((c) => (
                  <option key={c.id} value={c.id}>
                    {sourceLabel({ type: "rtsp", path_or_uri: c.rtsp_url })}
                  </option>
                ))}
              </select>
            </label>
          )}

          <input
            type="text"
            placeholder="rtsp://camera/stream"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />

          {/* Only for a camera that is actually remembered, so this
              does not offer to forget something it never knew. */}
          {cameras.some((c) => c.rtsp_url === url) && (
            <button
              type="button"
              className="forget-camera"
              onClick={() => {
                const camera = cameras.find((c) => c.rtsp_url === url);
                if (camera) forgetCamera(camera);
              }}
            >
              Forget this camera (its footage stays)
            </button>
          )}
          <label className="fps-control">
            Camera FPS
            <input
              type="number"
              min={1}
              value={expectedFps}
              onChange={(e) => setExpectedFps(Number(e.target.value))}
            />
          </label>
          {/* It reads like a speed setting and is not one. Saying so
              here is cheaper than the question it otherwise prompts:
              "I asked for 30 and it is running at 6". */}
          <p className="rtsp-hint">
            Roughly what the camera sends, so the tracker knows how long to wait before giving up on a
            vehicle. It does not set the speed - capture runs as fast as the camera delivers.
          </p>
          {/* manage=false: importing and removing belong in one place,
              and both pickers are in the same sidebar. */}
          <ModelPicker choice={modelChoice} id="live-model" disabled={starting} manage={false} />

          <label className="fps-control">
            Frames per vehicle
            <input
              type="number"
              min={0}
              max={50}
              aria-label="Frames per vehicle"
              value={perVehicle}
              onChange={(e) => setPerVehicle(Math.max(0, Math.min(50, Number(e.target.value))))}
            />
          </label>
          <p className="rtsp-hint">
            {perVehicle === 0
              ? "Every frame the model finds a plate in is kept - one vehicle can come back dozens of times."
              : `Each vehicle the model tracks is kept at most ${perVehicle} time(s): when it appears, then only as it comes closer or moves away. Saves labelling the same car over and over.`}
          </p>

          <label className="keep-frames">
            <input
              type="checkbox"
              aria-label="Find vehicles the model misses"
              checked={findMisses}
              onChange={(e) => setFindMisses(e.target.checked)}
            />
            Find vehicles the model misses
          </label>
          <p className="rtsp-hint">
            {findMisses
              ? "A small vehicle model also watches. A vehicle with no plate found on it is kept and marked \"possible miss\" in the Label tab - the frames most worth labelling. Only with a plate model."
              : "Off: misses are not looked for."}
          </p>

          <label className="keep-frames">
            <input
              type="checkbox"
              checked={keepFrames}
              onChange={(e) => setKeepFrames(e.target.checked)}
            />
            Save captured frames for labelling
          </label>
          {keepFrames ? (
            <>
              <label className="fps-control">
                Keep one frame in
                <input
                  type="number"
                  min={1}
                  value={keepEvery}
                  onChange={(e) => setKeepEvery(Math.max(1, Number(e.target.value)))}
                />
              </label>
              <p className="rtsp-hint">
                Kept frames go straight to the Label tab, whether or not the model found anything in them.
                Consecutive frames mostly show the same thing, so one in {keepEvery} costs little. Capped at
                2,000 frames per session, and you can delete the ones you did not label afterwards.
              </p>
            </>
          ) : (
            <p className="rtsp-hint">
              Off: only vehicles the model detects and tracks are saved. Turn this on if you want to label
              frames yourself.
            </p>
          )}
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
          {/* Only when it is being done, so the row does not read as
              "zero frames saved" to someone who did not ask for any. */}
          {(status.possible_misses_saved ?? 0) > 0 && (
            <div className="stat-row">
              <span>Possible misses kept</span>
              <strong>{status.possible_misses_saved}</strong>
            </div>
          )}
          {(status.frames_skipped_repeat ?? 0) > 0 && (
            <div className="stat-row">
              <span>Repeats of the same vehicle skipped</span>
              <strong>{status.frames_skipped_repeat}</strong>
            </div>
          )}
          {status.frames_saved > 0 && (
            <div className="stat-row">
              <span>Frames saved to label</span>
              <strong>{status.frames_saved}</strong>
            </div>
          )}
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
