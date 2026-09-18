import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import ActiveLearningPanel from "./components/ActiveLearningPanel";
import DatasetPanel from "./components/DatasetPanel";
import EvaluationPanel from "./components/EvaluationPanel";
import JobIndicator from "./components/JobIndicator";
import JobsPanel from "./components/JobsPanel";
import LivePreview from "./components/LivePreview";
import ProjectPicker from "./components/ProjectPicker";
import RtspPanel from "./components/RtspPanel";
import SourcePanel from "./components/SourcePanel";
import TrackBrowser from "./components/TrackBrowser";
import TrackReview from "./components/TrackReview";
import VideoPlayer from "./components/VideoPlayer";
import { IconArrowLeft, IconBox, IconBroadcast, IconChart, IconFilm, IconInbox } from "./Icons";
import { useJobs } from "./useJobs";
import type { Project, Source, Track } from "./types";

type Tab = "workflow" | "live" | "dataset" | "insights";
type MainView = "review" | "player" | "live";

const TABS: { id: Tab; label: string; icon: JSX.Element }[] = [
  { id: "workflow", label: "Sources", icon: <IconFilm /> },
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
  const { jobs, activeJobs, error: jobsError, refresh: refreshJobs, cancel: cancelJob } = useJobs(project?.id ?? null);

  const refreshTracks = useCallback(() => {
    if (!project) return;
    api.listTracks(project.id).then(setTracks);
  }, [project]);

  useEffect(refreshTracks, [refreshTracks]);

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
  }

  function handleReviewed(updated: Track) {
    setTracks((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
  }

  function selectTrack(track: Track) {
    setSelectedTrackId(track.id);
    setMainView("review");
    setTab("workflow");
  }

  function watchSource(source: Source) {
    setPlayerSource(source);
    setMainView("player");
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
              <SourcePanel project={project} onProcessed={refreshJobs} onWatch={watchSource} />
              <JobsPanel jobs={jobs} error={jobsError} onCancel={cancelJob} />
              <TrackBrowser tracks={tracks} selectedTrackId={selectedTrackId} onSelect={selectTrack} />
            </>
          )}
          {tab === "live" && (
            <RtspPanel project={project} onSessionEnded={refreshTracks} onShowPreview={showLivePreview} />
          )}
          {tab === "dataset" && <DatasetPanel project={project} />}
          {tab === "insights" && (
            <>
              <EvaluationPanel tracks={tracks} />
              <ActiveLearningPanel project={project} tracks={tracks} onSelectTrack={selectTrack} />
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
