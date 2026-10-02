import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { AuthProvider, useAuthSession } from "./auth";
import ActiveLearningPanel from "./components/ActiveLearningPanel";
import DatasetPanel from "./components/DatasetPanel";
import ClassSchemaEditor from "./components/ClassSchemaEditor";
import EvaluationPanel from "./components/EvaluationPanel";
import JobIndicator from "./components/JobIndicator";
import JobsPanel from "./components/JobsPanel";
import FrameGrid from "./components/FrameGrid";
import FrameNav from "./components/FrameNav";
import LabelCanvas from "./components/LabelCanvas";
import LabelBalancePanel from "./components/LabelBalancePanel";
import LabelQueue from "./components/LabelQueue";
import StoragePanel from "./components/StoragePanel";
import TrainingPanel from "./components/TrainingPanel";
import LivePreview from "./components/LivePreview";
import LoginScreen from "./components/LoginScreen";
import UserMenu from "./components/UserMenu";
import ProjectPicker from "./components/ProjectPicker";
import RtspPanel from "./components/RtspPanel";
import SchemaBanner from "./components/SchemaBanner";
import SourcePanel from "./components/SourcePanel";
import TrackBrowser from "./components/TrackBrowser";
import TrackReview from "./components/TrackReview";
import VideoPlayer from "./components/VideoPlayer";
import { IconArrowLeft, IconBox, IconBroadcast, IconChart, IconCheck, IconFilm, IconFolder } from "./Icons";
import { sourceLabel } from "./sourceLabel";
import { useFrameQueue } from "./useFrameQueue";
import { useJobs } from "./useJobs";
import type { Frame, Project, Source, Track } from "./types";

type Tab = "workflow" | "label" | "live" | "dataset" | "insights";
/** What the open module is showing: its own page, or one item in it. */
type MainView = "home" | "review" | "player" | "label";

const TABS: { id: Tab; label: string; icon: JSX.Element }[] = [
  { id: "workflow", label: "Sources", icon: <IconFilm /> },
  { id: "label", label: "Label", icon: <IconCheck /> },
  { id: "live", label: "Live", icon: <IconBroadcast /> },
  { id: "dataset", label: "Dataset", icon: <IconBox /> },
  { id: "insights", label: "Insights", icon: <IconChart /> },
];

function Studio() {
  const [project, setProject] = useState<Project | null>(null);
  const [tracks, setTracks] = useState<Track[]>([]);
  const [selectedTrackId, setSelectedTrackId] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("workflow");
  const [mainView, setMainView] = useState<MainView>("home");
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
    setMainView("home");
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
    setMainView("home");
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
      setMainView("home");
    }
    if (labelFrame?.source_id === sourceId) {
      setLabelFrame(null);
      setLabelDirty(false);
      setMainView("home");
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
    return (
      <div className="app-shell">
        <SchemaBanner />
        <AppHeader />
        <div className="app-shell__body app-shell__body--picker">
          <ProjectPicker onSelect={handleSelectProject} />
        </div>
      </div>
    );
  }

  const selectedTrack = tracks.find((t) => t.id === selectedTrackId) ?? null;
  const moduleInfo = TABS.find((t) => t.id === tab)!;

  /** A step into one item of a module, with the way back out. */
  function crumb(label: string, onBack: () => void) {
    return (
      <div className="module-crumb">
        <button className="back-link" onClick={onBack}>
          <IconArrowLeft /> {moduleInfo.label}
        </button>
        <span className="module-crumb__sep">/</span>
        <span className="module-crumb__here">{label}</span>
      </div>
    );
  }

  let page: JSX.Element;
  if (tab === "workflow" && mainView === "player" && playerSource) {
    page = (
      <>
        {crumb(sourceLabel(playerSource), () => setMainView("home"))}
        <VideoPlayer
          key={playerSource.id}
          project={project}
          source={playerSource}
          tracks={tracks}
          onSelectTrack={selectTrack}
          onClose={() => setMainView("home")}
        />
      </>
    );
  } else if (tab === "workflow" && mainView === "review" && selectedTrack) {
    page = (
      <>
        {crumb(`Track ${selectedTrack.tracker_track_id}`, () => setMainView("home"))}
        <TrackReview
          key={selectedTrack.id}
          project={project}
          track={selectedTrack}
          onReviewed={handleReviewed}
          onNavigateTrack={handleNavigateTrack}
          classesVersion={classesVersion}
        />
      </>
    );
  } else if (tab === "workflow") {
    page = (
      <ModulePage title="Sources" subtitle="Bring in footage, run detection and review what it found.">
        <div className="module-grid module-grid--sources">
          <section className="card module-card">
            <SourcePanel
              project={project}
              onProcessed={refreshJobs}
              onWatch={watchSource}
              onLabel={labelSource}
              onRemoved={handleSourceRemoved}
            />
          </section>
          <div className="module-stack">
            <section className="card module-card">
              <JobsPanel
                jobs={jobs}
                error={jobsError}
                onCancel={cancelJob}
                onDismiss={dismissJob}
                onClearFinished={clearFinishedJobs}
              />
            </section>
            <section className="card module-card">
              <TrackBrowser
                tracks={tracks}
                selectedTrackId={selectedTrackId}
                onSelect={selectTrack}
                project={project}
                onSwept={() => {
                  refreshTracks();
                  setQueueVersion((v) => v + 1);
                }}
              />
            </section>
          </div>
        </div>
      </ModulePage>
    );
  } else if (tab === "label" && mainView === "label" && labelFrame) {
    page = (
      <>
        {crumb(`Frame ${labelFrame.frame_index}`, () => setMainView("home"))}
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
        {/* Under the image, where the work is. */}
        <FrameNav queue={frameQueue} onShowGrid={() => setMainView("home")} />
      </>
    );
  } else if (tab === "label") {
    page = (
      <ModulePage title="Label" subtitle="Pick a frame and draw the boxes. Your place is remembered.">
        <div className="module-grid module-grid--label">
          <section className="card module-card">
            <LabelQueue queue={frameQueue} selectedFrameId={labelFrame?.id ?? null} project={project} />
          </section>
          <section className="card module-card">
            <FrameGrid
              frames={frameQueue.frames}
              selectedFrameId={labelFrame?.id ?? null}
              onOpen={(frame) => frameQueue.open(frame)}
            />
          </section>
        </div>
      </ModulePage>
    );
  } else if (tab === "live") {
    page = (
      <ModulePage title="Live" subtitle="Watch an RTSP camera with detection, and keep what it sees.">
        <div className="module-grid module-grid--live">
          <section className="card module-card">
            <RtspPanel project={project} onSessionEnded={refreshTracks} onShowPreview={showLivePreview} />
          </section>
          <section className="card module-card">
            {liveRunId ? (
              <LivePreview key={liveRunId} runId={liveRunId} onClose={() => setLiveRunId(null)} />
            ) : (
              <p className="module-empty">
                <IconBroadcast /> The live preview appears here once a capture starts.
              </p>
            )}
          </section>
        </div>
      </ModulePage>
    );
  } else if (tab === "dataset") {
    page = (
      <ModulePage title="Dataset" subtitle="Export versions, train models, and manage classes and disk.">
        <div className="module-grid module-grid--cards">
          <section className="card module-card">
            <DatasetPanel project={project} />
          </section>
          <section className="card module-card">
            <TrainingPanel project={project} jobs={jobs} refreshKey={queueVersion} />
          </section>
          <section className="card module-card">
            <ClassSchemaEditor project={project} onClassesChanged={() => setClassesVersion((v) => v + 1)} />
          </section>
          <section className="card module-card">
            <StoragePanel project={project} refreshKey={queueVersion} />
          </section>
        </div>
      </ModulePage>
    );
  } else {
    page = (
      <ModulePage title="Insights" subtitle="How the labels balance, how the model did, and what to look at next.">
        <div className="module-grid module-grid--cards">
          <section className="card module-card">
            <LabelBalancePanel project={project} refreshKey={queueVersion} />
          </section>
          <section className="card module-card">
            <EvaluationPanel tracks={tracks} />
          </section>
          <section className="card module-card">
            <ActiveLearningPanel
              project={project}
              tracks={tracks}
              onSelectTrack={selectTrack}
              onSelectFrame={selectFrameById}
            />
          </section>
        </div>
      </ModulePage>
    );
  }

  return (
    <div className="app-shell">
      {/* Above everything: a database behind the code breaks
          whichever panel happens to use the newest table, and the
          message there cannot explain why. */}
      <SchemaBanner onUpgraded={() => setQueueVersion((v) => v + 1)} />
      <AppHeader project={project} onProjects={() => handleSelectProject(null)} />
      <div className="app-shell__body">
        <aside className="sidebar">
          <nav className="module-nav" aria-label="Modules">
            {TABS.map((t) => (
              <button
                key={t.id}
                className={tab === t.id ? "selected" : ""}
                aria-current={tab === t.id ? "page" : undefined}
                onClick={() => setTab(t.id)}
              >
                {t.icon}
                <span>{t.label}</span>
              </button>
            ))}
          </nav>
        </aside>
        <main className="main-panel">
          <JobIndicator activeJobs={activeJobs} />
          <div className="main-panel__page">{page}</div>
        </main>
      </div>
    </div>
  );
}

function ModulePage({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) {
  return (
    <div className="module-page">
      <div className="module-page__head">
        <h1>{title}</h1>
        <p>{subtitle}</p>
      </div>
      {children}
    </div>
  );
}

/** The bar across the top: where you are, and who you are. */
function AppHeader({ project, onProjects }: { project?: Project; onProjects?: () => void }) {
  return (
    <header className="app-header">
      <div className="app-header__left">
        <span className="brand-icon brand-icon--sm" aria-hidden="true">
          <IconFolder />
        </span>
        <span className="app-header__brand">ANPR + ATCC Dataset Studio</span>
        {project && onProjects && (
          <span className="app-header__trail">
            <span className="app-header__sep">/</span>
            <button className="app-header__link" onClick={onProjects} title="Back to all projects">
              Projects
            </button>
            <span className="app-header__sep">/</span>
            <span className="app-header__project" title={project.name}>
              {project.name}
            </span>
          </span>
        )}
      </div>
      <UserMenu />
    </header>
  );
}

/**
 * Sign in first; then the studio, starting from choosing a project.
 *
 * The studio is keyed by who is signed in, so signing out and in as
 * someone else starts them from the project list rather than inside
 * whatever the last person had open.
 */
function App() {
  const auth = useAuthSession();
  const [schemaVersion, setSchemaVersion] = useState(0);

  let screen: JSX.Element;
  if (auth.checking) {
    screen = <div className="auth-screen" aria-busy="true" />;
  } else if (!auth.user) {
    screen = (
      <>
        <SchemaBanner onUpgraded={() => setSchemaVersion((v) => v + 1)} />
        <LoginScreen schemaVersion={schemaVersion} />
      </>
    );
  } else {
    screen = <Studio key={auth.user.id} />;
  }

  return <AuthProvider value={auth}>{screen}</AuthProvider>;
}

export default App;
