import type {
  Annotation,
  AtccClass,
  DatasetExportResult,
  DatasetVersion,
  DisagreementItem,
  EvaluationReport,
  Job,
  JobSubmitted,
  OcrCandidate,
  Project,
  ProcessingRun,
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
  createProject: (name: string) => request<Project>("/projects", { method: "POST", body: JSON.stringify({ name }) }),
  getClassSchema: (projectId: string) => request<AtccClass[]>(`/projects/${projectId}/class-schema`),

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

  listJobs: (params: { projectId?: string; status?: string; type?: string } = {}) => {
    const query = new URLSearchParams();
    if (params.projectId) query.set("project_id", params.projectId);
    if (params.status) query.set("status", params.status);
    if (params.type) query.set("type", params.type);
    const suffix = query.toString();
    return request<Job[]>(`/jobs${suffix ? `?${suffix}` : ""}`);
  },
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
