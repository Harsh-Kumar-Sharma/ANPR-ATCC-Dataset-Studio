import type {
  Annotation,
  AttributeDefinition,
  ClassDefinition,
  ClassDeleteOutcome,
  ClassUsage,
  DatasetExportResult,
  DatasetVersion,
  DisagreementItem,
  EvaluationReport,
  Frame,
  FrameAnnotationWrite,
  Job,
  JobSubmitted,
  LabelBalance,
  OcrCandidate,
  ProcessingRun,
  Project,
  ProjectClass,
  QueueProgress,
  QueueItem,
  RetrainingHandoffResult,
  RtspSessionStatus,
  RtspStartResult,
  Source,
  SourceDetections,
  Track,
  TrackReviewResult,
  TrackTimeline,
} from "./types";

// The backend is a local-only FastAPI server (see backend/README.md).
const API_BASE = "http://127.0.0.1:8000";

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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, body.code ?? "unknown_error", body.message ?? response.statusText);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
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

  listSources: (projectId: string) => request<Source[]>(`/projects/${projectId}/sources`),
  importSource: (projectId: string, path: string) =>
    request<Source>(`/projects/${projectId}/sources`, { method: "POST", body: JSON.stringify({ path }) }),
  /** Queues a detect+track run and returns immediately. Follow it with
   *  getJob or listJobs - the work is not done when this
   *  resolves. */
  processSource: (projectId: string, sourceId: string, targetFps: number) =>
    request<JobSubmitted>(`/projects/${projectId}/sources/${sourceId}/process`, {
      method: "POST",
      body: JSON.stringify({ sampling_config: { target_fps: targetFps } }),
    }),
  getProcessingRun: (runId: string) => request<ProcessingRun>(`/processing-runs/${runId}`),

  listJobs: (params: { projectId?: string; status?: string; type?: string } = {}) =>
    request<Job[]>(`/jobs${query({ project_id: params.projectId, status: params.status, type: params.type })}`),
  getJob: (jobId: string) => request<Job>(`/jobs/${jobId}`),
  /** Idempotent: cancelling an already-finished job returns it
   *  unchanged rather than failing. */
  cancelJob: (jobId: string) => request<Job>(`/jobs/${jobId}/cancel`, { method: "POST" }),
  sourceVideoUrl: (projectId: string, sourceId: string) => `${API_BASE}/projects/${projectId}/sources/${sourceId}/video`,
  getSourceDetections: (projectId: string, sourceId: string, runId?: string) =>
    request<SourceDetections>(
      `/projects/${projectId}/sources/${sourceId}/detections${runId ? `?run_id=${encodeURIComponent(runId)}` : ""}`,
    ),

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

  frameImageUrl: (frameCandidateId: string) => `${API_BASE}/tracks/frames/${frameCandidateId}/image`,

  listFrames: (projectId: string, status?: string) =>
    request<Frame[]>(`/projects/${projectId}/frames${query({ status })}`),
  getFrame: (frameId: string) => request<Frame>(`/frames/${frameId}`),
  getQueueProgress: (projectId: string) => request<QueueProgress>(`/projects/${projectId}/frames/progress`),
  /** Decide which of a source's frames are worth labelling. Runs in
   *  the background; frames a human has already touched are left alone. */
  selectFrames: (projectId: string, sourceId: string) =>
    request<JobSubmitted>(`/projects/${projectId}/sources/${sourceId}/select-frames`, { method: "POST" }),
  /** Skip a frame, or put a skipped one back. Boxes already on it
   *  are untouched either way. */
  setFrameStatus: (frameId: string, status: Frame["status"]) =>
    request<Frame>(`/frames/${frameId}/status`, { method: "PUT", body: JSON.stringify({ status }) }),
  /** The full frame, decoded on demand. The canvas's <img> src. */
  fullFrameImageUrl: (frameId: string) => `${API_BASE}/frames/${frameId}/image`,
  /** What a box can carry besides its class. One list, shared with the
   *  validation on the way back in. */
  listAttributeDefinitions: () => request<AttributeDefinition[]>("/annotation-attributes"),
  getFrameAnnotations: (frameId: string) => request<Annotation[]>(`/frames/${frameId}/annotations`),
  /** Whole-set replacement: whatever is not in the list is gone. */
  saveFrameAnnotations: (frameId: string, annotations: FrameAnnotationWrite[]) =>
    request<Annotation[]>(`/frames/${frameId}/annotations`, {
      method: "PUT",
      body: JSON.stringify({ annotations }),
    }),

  runOcr: (trackId: string) => request<OcrCandidate[]>(`/tracks/${trackId}/ocr`, { method: "POST" }),
  listOcrCandidates: (trackId: string) => request<OcrCandidate[]>(`/tracks/${trackId}/ocr-candidates`),
  selectOcrCandidate: (trackId: string, ocrCandidateId: string) =>
    request<OcrCandidate>(`/tracks/${trackId}/ocr-selection`, {
      method: "PUT",
      body: JSON.stringify({ ocr_candidate_id: ocrCandidateId }),
    }),
  correctOcr: (trackId: string, correctedText: string, frameCandidateId: string) =>
    request<OcrCandidate>(`/tracks/${trackId}/ocr-selection`, {
      method: "PUT",
      body: JSON.stringify({ corrected_text: correctedText, frame_candidate_id: frameCandidateId }),
    }),

  listDatasetVersions: (projectId: string) => request<DatasetVersion[]>(`/projects/${projectId}/dataset-versions`),
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

  startRtspSession: (projectId: string, rtspUrl: string, expectedFps: number) =>
    request<RtspStartResult>(`/projects/${projectId}/sources/rtsp/start`, {
      method: "POST",
      body: JSON.stringify({ rtsp_url: rtspUrl, expected_fps: expectedFps }),
    }),
  getRtspStatus: (runId: string) => request<RtspSessionStatus>(`/processing-runs/${runId}/rtsp/status`),
  stopRtspSession: (runId: string) =>
    request<RtspSessionStatus>(`/processing-runs/${runId}/rtsp/stop`, { method: "POST" }),
  rtspPreviewUrl: (runId: string) => `${API_BASE}/processing-runs/${runId}/rtsp/preview.jpg`,
};
