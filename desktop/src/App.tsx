import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import ActiveLearningPanel from "./components/ActiveLearningPanel";
import DatasetPanel from "./components/DatasetPanel";
import EvaluationPanel from "./components/EvaluationPanel";
import ProjectPicker from "./components/ProjectPicker";
import RtspPanel from "./components/RtspPanel";
import SourcePanel from "./components/SourcePanel";
import TrackBrowser from "./components/TrackBrowser";
import TrackReview from "./components/TrackReview";
import { IconArrowLeft, IconBox, IconBroadcast, IconChart, IconFilm, IconInbox } from "./Icons";
import type { Project, Track } from "./types";

type Tab = "workflow" | "live" | "dataset" | "insights";

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

  const refreshTracks = useCallback(() => {
    if (!project) return;
    api.listTracks(project.id).then(setTracks);
  }, [project]);

  useEffect(refreshTracks, [refreshTracks]);

  function handleSelectProject(p: Project | null) {
    setProject(p);
    setTracks([]);
    setSelectedTrackId(null);
    setTab("workflow");
  }

  function handleReviewed(updated: Track) {
    setTracks((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
  }

  function selectTrack(track: Track) {
    setSelectedTrackId(track.id);
    setTab("workflow");
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
              <SourcePanel project={project} onProcessed={refreshTracks} />
              <TrackBrowser tracks={tracks} selectedTrackId={selectedTrackId} onSelect={selectTrack} />
            </>
          )}
          {tab === "live" && <RtspPanel project={project} onSessionEnded={refreshTracks} />}
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
        {selectedTrack ? (
          <TrackReview
            key={selectedTrack.id}
            project={project}
            track={selectedTrack}
            onReviewed={handleReviewed}
            onNavigateTrack={handleNavigateTrack}
          />
        ) : (
          <div className="placeholder">
            <IconInbox />
            <p>Select a track from Sources to begin review.</p>
          </div>
        )}
      </main>
    </div>
  );
}

export default App;
