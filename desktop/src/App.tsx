import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import ActiveLearningPanel from "./components/ActiveLearningPanel";
import DatasetPanel from "./components/DatasetPanel";
import ClassSchemaEditor from "./components/ClassSchemaEditor";
import EvaluationPanel from "./components/EvaluationPanel";
import JobIndicator from "./components/JobIndicator";
import JobsPanel from "./components/JobsPanel";
import FrameNav from "./components/FrameNav";
import LabelCanvas from "./components/LabelCanvas";
import LabelBalancePanel from "./components/LabelBalancePanel";
import LabelQueue from "./components/LabelQueue";
import StoragePanel from "./components/StoragePanel";
import LivePreview from "./components/LivePreview";
import ProjectPicker from "./components/ProjectPicker";
import RtspPanel from "./components/RtspPanel";
import SourcePanel from "./components/SourcePanel";
import TrackBrowser from "./components/TrackBrowser";
import TrackReview from "./components/TrackReview";
import VideoPlayer from "./components/VideoPlayer";
import { IconArrowLeft, IconBox, IconBroadcast, IconChart, IconCheck, IconFilm, IconInbox } from "./Icons";
import { useFrameQueue } from "./useFrameQueue";
import { useJobs } from "./useJobs";
import type { Frame, Project, Source, Track } from "./types";

type Tab = "workflow" | "label" | "live" | "dataset" | "insights";
type MainView = "review" | "player" | "live" | "label";

const TABS: { id: Tab; label: string; icon: JSX.Element }[] = [
  { id: "workflow", label: "Sources", icon: <IconFilm /> },
  { id: "label", label: "Label", icon: <IconCheck /> },
  { id: "live", label: "Live", icon: <IconBroadcast /> },
  { id: "dataset", label: "Dataset", icon: <IconBox /> },
  { id: "insights", label: "Insights", icon: <IconChart /> },
];

function App() {
  const [project, setProject] = useState<Project | null>(null);
  const [tracks, setTracks] = useState<Track[]>([]);
  const [selectedTrackId, setSelectedTrackId] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("workflow");
  const [mainView, setMainView] = useState<MainView>("review");
  const [playerSource, setPlayerSource] = useState<Source | null>(null);
  const [liveRunId, setLiveRunId] = useState<string | null>(null);
  const [labelFrame, setLabelFrame] = useState<Frame | null>(null);
  // Which source the Label tab is narrowed to. Undefined means the app
  // has no opinion and the tab uses what it remembered; null means all
  // sources, chosen deliberately.
  const [labelSourceId, setLabelSourceId] = useState<string | null | undefined>(undefined);
  // Reported by the canvas so the queue will not walk away from
  // unsaved boxes without asking.
  const [labelDirty, setLabelDirty] = useState(false);
  // Bumped when a frame is saved so the queue's statuses catch up.
  const [queueVersion, setQueueVersion] = useState(0);
  // Bumped by the class editor so anything showing classes reloads them.
  const [classesVersion, setClassesVersion] = useState(0);
  const {
    jobs,
    activeJobs,
    error: jobsError,
    refresh: refreshJobs,
    cancel: cancelJob,
    dismiss: dismissJob,
    clearFinished: clearFinishedJobs,
  } = useJobs(project?.id ?? null);

  const refreshTracks = useCallback(() => {
    if (!project) return;
    api.listTracks(project.id).then(setTracks);
  }, [project]);

  useEffect(refreshTracks, [refreshTracks]);

  // A selected track that is no longer in the list has been deleted -
  // by removing its source, most likely. Holding the selection would
  // leave the main pane on a track that answers 404. Safe on first
  // load: nothing is selected until a list has been shown.
  useEffect(() => {
    if (selectedTrackId && !tracks.some((t) => t.id === selectedTrackId)) setSelectedTrackId(null);
  }, [tracks, selectedTrackId]);

  // A detect job finishes long after the click that started it, so the
  // track list has to react to the job going terminal rather than to
  // the request returning.
  const finishedDetectCount = jobs.filter((j) => j.type === "detect" && j.status === "succeeded").length;
  useEffect(refreshTracks, [finishedDetectCount, refreshTracks]);

  function handleSelectProject(p: Project | null) {
    setProject(p);
    setTracks([]);
    setSelectedTrackId(null);
    setTab("workflow");
    setMainView("review");
    setPlayerSource(null);
    setLiveRunId(null);
    setLabelFrame(null);
    setLabelSourceId(undefined);
  }

  function handleReviewed(updated: Track) {
    setTracks((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
  }

  function selectTrack(track: Track) {
    setSelectedTrackId(track.id);
    setMainView("review");
    setTab("workflow");
  }

  /** Clicking a source's name goes to its frames, and only its
   *  frames. The open frame is cleared: it probably belongs to a
   *  different clip, and leaving it on screen contradicts the list. */
  function labelSource(source: Source) {
    setLabelSourceId(source.id);
    setLabelFrame(null);
    setLabelDirty(false);
    setTab("label");
    setMainView("review");
  }

  /** A source was removed, taking its tracks and frames with it.
   *
   *  Everything showing them has to be told: the track list was left
   *  holding six rows that no longer existed, and clicking one
   *  answered "Track not found". */
  function handleSourceRemoved(sourceId: string) {
    refreshTracks();
    refreshJobs();
    setQueueVersion((v) => v + 1);
    if (playerSource?.id === sourceId) {
      setPlayerSource(null);
      setMainView("review");
    }
    if (labelFrame?.source_id === sourceId) {
      setLabelFrame(null);
      setLabelDirty(false);
      setMainView("review");
    }
    if (labelSourceId === sourceId) setLabelSourceId(undefined);
  }

  function watchSource(source: Source) {
    setPlayerSource(source);
    setMainView("player");
  }

  // Held here rather than in the queue panel: the Previous/Next under
  // the canvas walks the same list, and two copies would be two lists
  // disagreeing about which frame comes next.
  const frameQueue = useFrameQueue({
    project,
    selectedFrameId: labelFrame?.id ?? null,
    dirty: labelDirty,
    refreshKey: queueVersion,
    sourceId: labelSourceId,
    onSelect: (frame) => labelFrameNow(frame),
    onSourceChange: setLabelSourceId,
  });

  function labelFrameNow(frame: Frame) {
    setLabelFrame(frame);
    setLabelDirty(false);
    setMainView("label");
  }

  /** Open a frame the user picked somewhere other than the queue - the
   *  disagreement list, for one, whose canvas-drawn entries have a frame
   *  and no track. The frame is fetched rather than looked up locally
   *  because nothing outside the Label tab holds the queue. */
  async function selectFrameById(frameId: string) {
    // Deliberately lets the failure through. The app has no toast, so
    // the panel the user clicked from is the only place that can say
    // anything - and a button that silently does nothing is worse than
    // one that says why.
    const frame = await api.getFrame(frameId);
    setTab("label");
    labelFrameNow(frame);
  }

  function showLivePreview(runId: string) {
    setLiveRunId(runId);
    setMainView("live");
  }

  function handleNavigateTrack(direction: 1 | -1) {
    if (tracks.length === 0) return;
    const currentIndex = tracks.findIndex((t) => t.id === selectedTrackId);
    const nextIndex = currentIndex + direction;
    if (nextIndex >= 0 && nextIndex < tracks.length) {
      setSelectedTrackId(tracks[nextIndex].id);
    }
  }

  if (!project) {
    return <ProjectPicker onSelect={handleSelectProject} />;
  }

  const selectedTrack = tracks.find((t) => t.id === selectedTrackId) ?? null;

  let main: JSX.Element;
  if (mainView === "player" && playerSource) {
    main = (
      <VideoPlayer
        key={playerSource.id}
        project={project}
        source={playerSource}
        tracks={tracks}
        onSelectTrack={selectTrack}
        onClose={() => setMainView("review")}
      />
    );
  } else if (mainView === "label" && labelFrame) {
    main = (
      <>
        <LabelCanvas
          key={labelFrame.id}
          project={project}
          frame={labelFrame}
          classesVersion={classesVersion}
          onSaved={() => setQueueVersion((v) => v + 1)}
          onDirtyChange={setLabelDirty}
          onRejected={() => {
            // The frame leaves the queue, but the user is still labelling -
            // the queue picks the next one rather than dropping them out.
            setLabelDirty(false);
            setLabelFrame(null);
            setQueueVersion((v) => v + 1);
          }}
          onDeleted={() => {
            // Same move as a skip. The difference is behind it: there is
            // no frame left to put back.
            setLabelDirty(false);
            setLabelFrame(null);
            setQueueVersion((v) => v + 1);
          }}
        />
        {/* Under the image, where the work is - not only in the
            sidebar the user has to look away to reach. */}
        <FrameNav queue={frameQueue} />
      </>
    );
  } else if (mainView === "live" && liveRunId) {
    main = <LivePreview key={liveRunId} runId={liveRunId} onClose={() => setMainView("review")} />;
  } else if (selectedTrack) {
    main = (
      <TrackReview
        key={selectedTrack.id}
        project={project}
        track={selectedTrack}
        onReviewed={handleReviewed}
        onNavigateTrack={handleNavigateTrack}
        classesVersion={classesVersion}
      />
    );
  } else {
    main = (
      <div className="placeholder">
        <IconInbox />
        <p>Select a track to review it, or press Watch on a source to play it with detection boxes.</p>
      </div>
    );
  }

  return (
    <div className="app-layout">
      <aside className="sidebar">
        <div className="sidebar-header">
          <button className="back-link" onClick={() => handleSelectProject(null)}>
            <IconArrowLeft /> Projects
          </button>
          <h2>{project.name}</h2>
        </div>

        <nav className="tab-bar">
          {TABS.map((t) => (
            <button key={t.id} className={tab === t.id ? "selected" : ""} onClick={() => setTab(t.id)}>
              {t.icon}
              {t.label}
            </button>
          ))}
        </nav>

        <div className="sidebar-content">
          {tab === "workflow" && (
            <>
              <SourcePanel
                project={project}
                onProcessed={refreshJobs}
                onWatch={watchSource}
                onLabel={labelSource}
                onRemoved={handleSourceRemoved}
              />
              <JobsPanel
                jobs={jobs}
                error={jobsError}
                onCancel={cancelJob}
                onDismiss={dismissJob}
                onClearFinished={clearFinishedJobs}
              />
              <TrackBrowser tracks={tracks} selectedTrackId={selectedTrackId} onSelect={selectTrack} />
            </>
          )}
          {tab === "label" && (
            <LabelQueue queue={frameQueue} selectedFrameId={labelFrame?.id ?? null} />
          )}
          {tab === "live" && (
            <RtspPanel project={project} onSessionEnded={refreshTracks} onShowPreview={showLivePreview} />
          )}
          {tab === "dataset" && (
            <>
              <DatasetPanel project={project} />
              <StoragePanel project={project} refreshKey={queueVersion} />
              <ClassSchemaEditor project={project} onClassesChanged={() => setClassesVersion((v) => v + 1)} />
            </>
          )}
          {tab === "insights" && (
            <>
              <LabelBalancePanel project={project} refreshKey={queueVersion} />
              <EvaluationPanel tracks={tracks} />
              <ActiveLearningPanel
                project={project}
                tracks={tracks}
                onSelectTrack={selectTrack}
                onSelectFrame={selectFrameById}
              />
            </>
          )}
        </div>
      </aside>
      <main className="main-panel">
        <JobIndicator activeJobs={activeJobs} />
        {main}
      </main>
    </div>
  );
}

export default App;
