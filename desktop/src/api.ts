import type {
  Annotation,
  AttributeDefinition,
  AuthStatus,
  SignedIn,
  User,
  UserRole,
  ClassDefinition,
  ClassDeleteOutcome,
  ClassUsage,
  DatasetExportResult,
  DatasetVersion,
  DeletedFrame,
  DisagreementItem,
  EvaluationReport,
  Frame,
  FrameAnnotationWrite,
  Job,
  JobSubmitted,
  LabelingTask,
  LabelBalance,
  OcrCandidate,
  PlateReading,
  ProcessingRun,
  Project,
  ProjectClass,
  Reclaimed,
  ProjectContents,
  QueueProgress,
  QueueItem,
  RetrainingHandoffResult,
  RtspSessionStatus,
  RtspStartResult,
  LiveCamera,
  ModelInfo,
  RunEstimate,
  SchemaState,
  SuggestedBox,
  SchemaUpgradeResult,
  Sweep,
  TrackSweep,
  TrainingRun,
  TrainingStarted,
  Source,
  SourceContents,
  SourceQueue,
  StorageUsage,
  SourceDetections,
  Track,
  TrackReviewResult,
  TrackTimeline,
} from "./types";

// The backend is a local-only FastAPI server (see backend/README.md).
const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

/** Build a query string, dropping unset values. Returns "" or "?a=b&c=d". */
function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === false || value === "") continue;
    search.set(key, String(value));
  }
  const suffix = search.toString();
  return suffix ? `?${suffix}` : "";
}

/** Where the sign-in token is kept between launches. Browser storage:
 *  it is this machine's sign-in, not the project's. */
const TOKEN_KEY = "anpr:auth-token";

let authToken: string | null = readToken();

function readToken(): string | null {
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function getAuthToken(): string | null {
  return authToken;
}

export function setAuthToken(token: string | null): void {
  authToken = token;
  try {
    if (token === null) window.localStorage.removeItem(TOKEN_KEY);
    else window.localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // Private mode: signed in for this window only.
  }
}

/** Told when the server says the sign-in is no longer good - expired,
 *  signed out elsewhere, or the account switched off - so the app can
 *  go back to the sign-in screen instead of failing panel by panel. */
let unauthorizedHandler: (() => void) | null = null;

export function onUnauthorized(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

/** A URL the browser fetches by itself - an <img>, a <video>, a
 *  download - cannot carry a header, so the token rides in the query. */
function withToken(url: string): string {
  if (!authToken) return url;
  return `${url}${url.includes("?") ? "&" : "?"}access_token=${encodeURIComponent(authToken)}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    // Not for the sign-in calls themselves: a wrong password is an
    // answer for the form, not a reason to reset the app.
    if (response.status === 401 && !path.startsWith("/auth/login") && !path.startsWith("/auth/setup")) {
      unauthorizedHandler?.();
    }
    throw new ApiError(response.status, body.code ?? "unknown_error", body.message ?? response.statusText);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  // --- signing in -----------------------------------------------------------
  getAuthStatus: () => request<AuthStatus>("/auth/status"),
  /** Create the first admin. Only works while nobody exists. */
  setupAdmin: (username: string, password: string, displayName: string) =>
    request<SignedIn>("/auth/setup", {
      method: "POST",
      body: JSON.stringify({ username, password, display_name: displayName }),
    }),
  login: (username: string, password: string) =>
    request<SignedIn>("/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  getMe: () => request<User>("/auth/me"),
  changeMyPassword: (currentPassword: string, newPassword: string) =>
    request<void>("/auth/me/password", {
      method: "PUT",
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    }),

  // --- managing users (admin) -------------------------------------------------
  listUsers: () => request<User[]>("/users"),
  createUser: (payload: { username: string; password: string; display_name: string; role: UserRole }) =>
    request<User>("/users", { method: "POST", body: JSON.stringify(payload) }),
  updateUser: (userId: string, changes: { display_name?: string; role?: UserRole; is_active?: boolean }) =>
    request<User>(`/users/${userId}`, { method: "PATCH", body: JSON.stringify(changes) }),
  resetUserPassword: (userId: string, newPassword: string) =>
    request<void>(`/users/${userId}/password`, { method: "PUT", body: JSON.stringify({ new_password: newPassword }) }),
  deleteUser: (userId: string) => request<void>(`/users/${userId}`, { method: "DELETE" }),

  // --- labelling tasks ----------------------------------------------------------
  /** Every task in the project for an admin; only your own for a user. */
  listTasks: (projectId: string) => request<LabelingTask[]>(`/projects/${projectId}/tasks`),
  getTask: (taskId: string) => request<LabelingTask>(`/tasks/${taskId}`),
  createTask: (projectId: string, sourceId: string, assigneeId: string) =>
    request<LabelingTask>(`/projects/${projectId}/tasks`, {
      method: "POST",
      body: JSON.stringify({ source_id: sourceId, assignee_id: assigneeId }),
    }),
  reassignTask: (taskId: string, assigneeId: string) =>
    request<LabelingTask>(`/tasks/${taskId}`, { method: "PATCH", body: JSON.stringify({ assignee_id: assigneeId }) }),
  /** The assignee has begun. Harmless to repeat, and a no-op for an admin looking in. */
  startTask: (taskId: string) => request<LabelingTask>(`/tasks/${taskId}/start`, { method: "POST" }),
  /** Refused while any frame is still pending. */
  submitTask: (taskId: string) => request<LabelingTask>(`/tasks/${taskId}/submit`, { method: "POST" }),
  acceptTask: (taskId: string) => request<LabelingTask>(`/tasks/${taskId}/accept`, { method: "POST" }),
  rejectTask: (taskId: string, note: string) =>
    request<LabelingTask>(`/tasks/${taskId}/reject`, { method: "POST", body: JSON.stringify({ note }) }),
  reopenTask: (taskId: string) => request<LabelingTask>(`/tasks/${taskId}/reopen`, { method: "POST" }),
  /** The task only - the frames and their labels stay. */
  deleteTask: (taskId: string) => request<void>(`/tasks/${taskId}`, { method: "DELETE" }),

  /** What deleting a project would destroy. Its own call because a
   *  confirmation that cannot say what is about to go is not one. */
  getProjectContents: (projectId: string) => request<ProjectContents>(`/projects/${projectId}/contents`),
  /** Irreversible. The name must match the project's exactly - the
   *  interlock against a mis-aimed request. */
  deleteProject: (projectId: string, name: string) =>
    request<ProjectContents>(`/projects/${projectId}`, { method: "DELETE", body: JSON.stringify({ name }) }),
  /** Whether the database matches this app's expectations. */
  getSchemaState: () => request<SchemaState>("/schema"),

  /** Bring the database up to date. Backs it up first. */
  upgradeSchema: () => request<SchemaUpgradeResult>("/schema/upgrade", { method: "POST" }),

  listProjects: () => request<Project[]>("/projects"),
  createProject: (name: string, classPreset?: string) =>
    request<Project>("/projects", {
      method: "POST",
      body: JSON.stringify({ name, class_preset: classPreset }),
    }),
  getClassSchema: (projectId: string) => request<ProjectClass[]>(`/projects/${projectId}/class-schema`),

  listClasses: (projectId: string) => request<ClassDefinition[]>(`/projects/${projectId}/classes`),
  createClass: (projectId: string, name: string) =>
    request<ClassDefinition>(`/projects/${projectId}/classes`, { method: "POST", body: JSON.stringify({ name }) }),
  renameClass: (projectId: string, classId: number, name: string) =>
    request<ClassDefinition>(`/projects/${projectId}/classes/${classId}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }),
  getClassUsage: (projectId: string, classId: number) =>
    request<ClassUsage>(`/projects/${projectId}/classes/${classId}/usage`),
  /** A class in use is refused unless told what happens to its labels:
   *  `remapTo` moves them onto another class (also how two classes are
   *  merged), `deleteLabels` removes them with it. */
  deleteClass: (projectId: string, classId: number, options: { remapTo?: number; deleteLabels?: boolean } = {}) =>
    request<ClassDeleteOutcome>(
      `/projects/${projectId}/classes/${classId}${query({ remap_to: options.remapTo, delete_labels: options.deleteLabels })}`,
      { method: "DELETE" },
    ),

  /** What removing a source would destroy. Its own call, because a
   *  confirmation that cannot say what is about to go is not one. */
  getSourceContents: (projectId: string, sourceId: string) =>
    request<SourceContents>(`/projects/${projectId}/sources/${sourceId}/contents`),
  /** Irreversible. Refused while a job or a live capture is running
   *  against the source. */
  deleteSource: (projectId: string, sourceId: string) =>
    request<SourceContents>(`/projects/${projectId}/sources/${sourceId}`, { method: "DELETE" }),
  /** Give a source a name of its own. Empty clears it. */
  renameSource: (projectId: string, sourceId: string, name: string) =>
    request<Source>(`/projects/${projectId}/sources/${sourceId}`, { method: "PATCH", body: JSON.stringify({ name }) }),
  listSources: (projectId: string) => request<Source[]>(`/projects/${projectId}/sources`),
  importSource: (projectId: string, path: string) =>
    request<Source>(`/projects/${projectId}/sources`, { method: "POST", body: JSON.stringify({ path }) }),
  /** Queues a detect+track run and returns immediately. Follow it with
   *  getJob or listJobs - the work is not done when this
   *  resolves. */
  processSource: (
    projectId: string,
    sourceId: string,
    targetFps: number,
    modelId?: string | null,
    everyFrame = false,
  ) =>
    request<JobSubmitted>(`/projects/${projectId}/sources/${sourceId}/process`, {
      method: "POST",
      body: JSON.stringify({
        sampling_config: { target_fps: targetFps, every_frame: everyFrame },
        model_id: modelId ?? null,
      }),
    }),

  /** What a run would process and cost, asked before the button is
   *  pressed rather than found out at 80% on a full disk. */
  estimateRun: (projectId: string, sourceId: string, targetFps: number, everyFrame: boolean) =>
    request<RunEstimate>(
      `/projects/${projectId}/sources/${sourceId}/process/estimate${query({
        target_fps: targetFps,
        every_frame: everyFrame,
      })}`,
    ),

  /** Everything that can detect: the built-ins, and your own. */
  /** Models this project can use: the built-ins, its own, and any
   *  nobody has claimed. */
  listModels: (projectId?: string) =>
    request<ModelInfo[]>(`/models${query({ project_id: projectId })}`),

  /** Take a .pt you trained into the app. The backend loads it as
   *  part of accepting it, so this is slow and can refuse. */
  importModel: (path: string, name?: string, projectId?: string) =>
    request<ModelInfo>("/models", {
      method: "POST",
      body: JSON.stringify({ path, name: name?.trim() || null, project_id: projectId ?? null }),
    }),

  /** Forget a model you imported. Built-ins are refused. */
  deleteModel: (modelId: string) =>
    request<void>(`/models/${encodeURIComponent(modelId)}`, { method: "DELETE" }),
  getProcessingRun: (runId: string) => request<ProcessingRun>(`/processing-runs/${runId}`),

  listJobs: (params: { projectId?: string; status?: string; type?: string } = {}) =>
    request<Job[]>(`/jobs${query({ project_id: params.projectId, status: params.status, type: params.type })}`),
  getJob: (jobId: string) => request<Job>(`/jobs/${jobId}`),
  /** Idempotent: cancelling an already-finished job returns it
   *  unchanged rather than failing. */
  cancelJob: (jobId: string) => request<Job>(`/jobs/${jobId}/cancel`, { method: "POST" }),
  /** Forget a finished job, its progress file and its log. Refused
   *  while it is still pending or running - cancel it first. */
  dismissJob: (jobId: string) => request<{ removed: number }>(`/jobs/${jobId}`, { method: "DELETE" }),
  /** Forget every finished job of a project in one go. */
  clearFinishedJobs: (projectId: string) =>
    request<{ removed: number }>("/jobs/clear-finished", {
      method: "POST",
      body: JSON.stringify({ project_id: projectId }),
    }),
  sourceVideoUrl: (projectId: string, sourceId: string) =>
    withToken(`${API_BASE}/projects/${projectId}/sources/${sourceId}/video`),
  getSourceDetections: (projectId: string, sourceId: string, runId?: string) =>
    request<SourceDetections>(
      `/projects/${projectId}/sources/${sourceId}/detections${runId ? `?run_id=${encodeURIComponent(runId)}` : ""}`,
    ),

  /** What deleting the unaccepted detections would take. */
  previewTrackSweep: (projectId: string) =>
    request<TrackSweep>(`/projects/${projectId}/tracks/sweep`),

  /** Keep what was accepted or flagged; delete the rest. */
  sweepUnacceptedTracks: (projectId: string) =>
    request<TrackSweep>(`/projects/${projectId}/tracks/sweep`, { method: "POST" }),

  listTracks: (projectId: string) => request<Track[]>(`/projects/${projectId}/tracks`),
  getTrackTimeline: (trackId: string) => request<TrackTimeline>(`/tracks/${trackId}`),
  getAnnotation: async (trackId: string): Promise<Annotation | null> => {
    try {
      return await request<Annotation>(`/tracks/${trackId}/annotation`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null;
      throw e;
    }
  },
  submitReview: (
    trackId: string,
    payload: { frame_candidate_id: string; decision: string; class_id?: number | null; bbox_json?: number[] },
  ) => request<TrackReviewResult>(`/tracks/${trackId}/review`, { method: "PUT", body: JSON.stringify(payload) }),

  frameImageUrl: (frameCandidateId: string) => withToken(`${API_BASE}/tracks/frames/${frameCandidateId}/image`),

  /** The labelling queue, narrowed to one source when given one -
   *  which is the only way it stays usable past the first clip. */
  listFrames: (projectId: string, status?: string, sourceId?: string | null) =>
    request<Frame[]>(`/projects/${projectId}/frames${query({ status, source_id: sourceId ?? undefined })}`),
  getFrame: (frameId: string) => request<Frame>(`/frames/${frameId}`),
  /** Narrowed by the same source as the list. A progress line counting
   *  the whole project beside a list showing one clip is worse than no
   *  progress line. */
  getQueueProgress: (projectId: string, sourceId?: string | null) =>
    request<QueueProgress>(
      `/projects/${projectId}/frames/progress${query({ source_id: sourceId ?? undefined })}`,
    ),
  /** Each source with its own progress, most work left first. One call
   *  rather than one per source. */
  getQueueBySource: (projectId: string) => request<SourceQueue[]>(`/projects/${projectId}/frames/by-source`),
  /** Decide which of a source's frames are worth labelling. Runs in
   *  the background; frames a human has already touched are left alone. */
  selectFrames: (projectId: string, sourceId: string) =>
    request<JobSubmitted>(`/projects/${projectId}/sources/${sourceId}/select-frames`, { method: "POST" }),
  /** Skip a frame, or put a skipped one back. Boxes already on it
   *  are untouched either way. */
  setFrameStatus: (frameId: string, status: Frame["status"]) =>
    request<Frame>(`/frames/${frameId}/status`, { method: "PUT", body: JSON.stringify({ status }) }),
  /** This dataset version as one zip, for training on another
   *  machine. Streamed by the server, so it is a URL rather than a
   *  fetch: a 13 MB body held in memory to re-serve it is pointless
   *  when the browser can save it straight to disk. */
  datasetArchiveUrl: (datasetVersionId: string) =>
    withToken(`${API_BASE}/dataset-versions/${datasetVersionId}/archive`),

  /** The full frame, decoded on demand. The canvas's <img> src. */
  fullFrameImageUrl: (frameId: string) => withToken(`${API_BASE}/frames/${frameId}/image`),

  /** A small picture of a frame, for showing many at once. Its own
   *  endpoint because it never decodes a full-size image to disk. */
  frameThumbnailUrl: (frameId: string) => withToken(`${API_BASE}/frames/${frameId}/thumbnail`),

  /** What the model found on a frame, for the canvas to open with. */
  getFrameSuggestions: (frameId: string) =>
    request<SuggestedBox[]>(`/frames/${frameId}/suggestions`),
  /** What a box can carry besides its class. One list, shared with the
   *  validation on the way back in. */
  listAttributeDefinitions: () => request<AttributeDefinition[]>("/annotation-attributes"),
  /** Remove a frame for good - its boxes, its detections and its
   *  decoded image. Not the same as skipping, which is reversible.
   *  Refused for a frame in an exported dataset version. */
  deleteFrame: (frameId: string) => request<DeletedFrame>(`/frames/${frameId}`, { method: "DELETE" }),
  getFrameAnnotations: (frameId: string) => request<Annotation[]>(`/frames/${frameId}/annotations`),
  /** Whole-set replacement: whatever is not in the list is gone. */
  saveFrameAnnotations: (frameId: string, annotations: FrameAnnotationWrite[]) =>
    request<Annotation[]>(`/frames/${frameId}/annotations`, {
      method: "PUT",
      body: JSON.stringify({ annotations }),
    }),

  runOcr: (trackId: string) => request<OcrCandidate[]>(`/tracks/${trackId}/ocr`, { method: "POST" }),
  listOcrCandidates: (trackId: string) => request<OcrCandidate[]>(`/tracks/${trackId}/ocr-candidates`),
  /** Record what a human read off this track's plate. It goes on the
   *  annotation - the one home for a plate - so the track must have been
   *  reviewed first. Empty clears it. */
  setTrackPlateText: (trackId: string, plateText: string) =>
    request<Annotation>(`/tracks/${trackId}/plate-text`, {
      method: "PUT",
      body: JSON.stringify({ plate_text: plateText }),
    }),
  /** What the model read on this frame, most confident first. By frame
   *  because the labelling canvas has no track. */
  getFramePlateReadings: (frameId: string) =>
    request<PlateReading[]>(`/frames/${frameId}/plate-readings`),

  /** Where the disk went, across every project, plus what is left on
   *  the drive. */
  getStorageUsage: () => request<StorageUsage>("/storage"),
  /** Delete decoded frame images. They are a cache of the source
   *  video and come back the next time a frame is opened; labels and
   *  frame rows are untouched. */
  clearFrameImages: (projectId: string, sourceId?: string) =>
    request<Reclaimed>("/storage/clear-frame-images", {
      method: "POST",
      body: JSON.stringify({ project_id: projectId, source_id: sourceId ?? null }),
    }),
  /** Remove the progress file and log of every finished job. */
  clearJobFiles: () => request<Reclaimed>("/storage/clear-job-files", { method: "POST" }),
  /** Delete workspace directories belonging to no project. */
  removeOrphanWorkspaces: () => request<Reclaimed>("/storage/remove-orphan-workspaces", { method: "POST" }),
  /** Delete an exported dataset version and its files. The labels it
   *  was made from stay - the version is a snapshot, not the work. */
  deleteDatasetVersion: (datasetVersionId: string) =>
    request<Reclaimed>(`/dataset-versions/${datasetVersionId}`, { method: "DELETE" }),
  listDatasetVersions: (projectId: string) => request<DatasetVersion[]>(`/projects/${projectId}/dataset-versions`),

  /** Start training on an exported dataset version. Returns as soon
   *  as it is queued - follow it through the job. */
  startTraining: (
    projectId: string,
    options: { datasetVersionId: string; baseModelId: string; epochs: number; imageSize: number },
  ) =>
    request<TrainingStarted>(`/projects/${projectId}/training-runs`, {
      method: "POST",
      body: JSON.stringify({
        dataset_version_id: options.datasetVersionId,
        base_model_id: options.baseModelId,
        epochs: options.epochs,
        image_size: options.imageSize,
      }),
    }),

  listTrainingRuns: (projectId: string) =>
    request<TrainingRun[]>(`/projects/${projectId}/training-runs`),

  /** Take one finished run off the list. Its model stays. */
  forgetTrainingRun: (runId: string) =>
    request<void>(`/training-runs/${runId}`, { method: "DELETE" }),

  /** Clear every finished run off the list. Models stay. */
  clearFinishedTrainingRuns: (projectId: string) =>
    request<{ forgotten: number }>(`/projects/${projectId}/training-runs`, { method: "DELETE" }),
  exportDataset: (projectId: string) =>
    request<DatasetExportResult>(`/projects/${projectId}/dataset-versions`, { method: "POST", body: JSON.stringify({}) }),

  freezeSource: (projectId: string, sourceId: string, groundTruthVehicleCount: number) =>
    request<Source>(`/projects/${projectId}/sources/${sourceId}/freeze`, {
      method: "PUT",
      body: JSON.stringify({ ground_truth_vehicle_count: groundTruthVehicleCount }),
    }),
  getRunEvaluation: (runId: string) => request<EvaluationReport>(`/processing-runs/${runId}/evaluation`),

  getLowConfidenceQueue: (projectId: string) =>
    request<QueueItem[]>(`/projects/${projectId}/active-learning/low-confidence-queue`),
  getHardFailedQueue: (projectId: string) =>
    request<QueueItem[]>(`/projects/${projectId}/active-learning/hard-failed-queue`),
  /** This project's human labels counted by class, whichever way they
   *  were written. Not the evaluation report's run-scoped distribution. */
  getLabelBalance: (projectId: string) => request<LabelBalance>(`/projects/${projectId}/label-balance`),
  getDisagreements: (projectId: string) =>
    request<DisagreementItem[]>(`/projects/${projectId}/active-learning/disagreements`),
  createRetrainingHandoff: (datasetVersionId: string) =>
    request<RetrainingHandoffResult>(`/dataset-versions/${datasetVersionId}/retraining-handoff`, { method: "POST" }),

  startRtspSession: (
    projectId: string,
    rtspUrl: string,
    expectedFps: number,
    modelId?: string | null,
    keep?: { keepFrames: boolean; every: number },
  ) =>
    request<RtspStartResult>(`/projects/${projectId}/sources/rtsp/start`, {
      method: "POST",
      body: JSON.stringify({
        rtsp_url: rtspUrl,
        expected_fps: expectedFps,
        model_id: modelId ?? null,
        keep_frames: keep?.keepFrames ?? false,
        keep_every: keep?.every ?? 10,
      }),
    }),

  /** What deleting this project's (or source's) unlabelled frames
   *  would take. Asked before the button. */
  previewSweep: (projectId: string, sourceId?: string | null) =>
    request<Sweep>(`/projects/${projectId}/frames/sweep${query({ source_id: sourceId ?? undefined })}`),

  /** Keep the labelled frames, delete the rest for good. */
  sweepUnlabelled: (projectId: string, sourceId?: string | null) =>
    request<Sweep>(`/projects/${projectId}/frames/sweep${query({ source_id: sourceId ?? undefined })}`, {
      method: "POST",
    }),
  /** Cameras this project has watched, most recent first. */
  listLiveCameras: (projectId: string) =>
    request<LiveCamera[]>(`/projects/${projectId}/sources/rtsp/cameras`),

  /** Stop offering a camera. Its footage is untouched. */
  forgetLiveCamera: (projectId: string, cameraId: string) =>
    request<void>(`/projects/${projectId}/sources/rtsp/cameras/${cameraId}`, { method: "DELETE" }),

  getRtspStatus: (runId: string) => request<RtspSessionStatus>(`/processing-runs/${runId}/rtsp/status`),
  stopRtspSession: (runId: string) =>
    request<RtspSessionStatus>(`/processing-runs/${runId}/rtsp/stop`, { method: "POST" }),
  rtspPreviewUrl: (runId: string) => withToken(`${API_BASE}/processing-runs/${runId}/rtsp/preview.jpg`),
};
