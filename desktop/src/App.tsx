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
import type { Project, Track } from "./types";

function App() {
  const [project, setProject] = useState<Project | null>(null);
  const [tracks, setTracks] = useState<Track[]>([]);
  const [selectedTrackId, setSelectedTrackId] = useState<string | null>(null);

  const refreshTracks = useCallback(() => {
    if (!project) return;
    api.listTracks(project.id).then(setTracks);
  }, [project]);

  useEffect(refreshTracks, [refreshTracks]);

  function handleSelectProject(p: Project | null) {
    setProject(p);
    setTracks([]);
    setSelectedTrackId(null);
  }

  function handleReviewed(updated: Track) {
    setTracks((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
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
        <button className="back-link" onClick={() => handleSelectProject(null)}>
          &larr; Projects
        </button>
        <h2>{project.name}</h2>
        <SourcePanel project={project} onProcessed={refreshTracks} />
        <RtspPanel project={project} onSessionEnded={refreshTracks} />
        <TrackBrowser tracks={tracks} selectedTrackId={selectedTrackId} onSelect={(t) => setSelectedTrackId(t.id)} />
        <DatasetPanel project={project} />
        <EvaluationPanel tracks={tracks} />
        <ActiveLearningPanel project={project} tracks={tracks} onSelectTrack={(t) => setSelectedTrackId(t.id)} />
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
          <p className="placeholder">Select a track to begin review.</p>
        )}
      </main>
    </div>
  );
}

export default App;
