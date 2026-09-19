export interface Project {
  id: string;
  name: string;
  created_at: string;
  class_schema_version: string;
  workspace_path: string;
}

export interface Source {
  id: string;
  project_id: string;
  type: string;
  path_or_uri: string;
  fps: number;
  width: number;
  height: number;
  duration_ms: number;
  frame_count: number;
  created_at: string;
  is_frozen: boolean;
  ground_truth_vehicle_count: number | null;
  is_processing: boolean;
}

export interface ProcessingRun {
  id: string;
  source_id: string;
  detector_version: string | null;
  tracker_config: Record<string, unknown> | null;
  sampling_config: Record<string, unknown>;
  status: string;
  sampled_frame_count: number | null;
  error_message: string | null;
  started_at: string;
  completed_at: string | null;
}

export type TrackBucket = "BEST_DETECTION" | "HARD" | "FAILED";
export type ReviewStatus = "unreviewed" | "accepted" | "hard" | "failed";

export interface Track {
  id: string;
  run_id: string;
  tracker_track_id: number;
  start_ts: number;
  end_ts: number;
  bucket: TrackBucket | null;
  review_status: ReviewStatus;
  created_at: string;
}

export interface FrameCandidateFlags {
  truncated: boolean;
  quality_score: number;
  roles: string[];
}

export interface FrameCandidate {
  id: string;
  track_id: string;
  frame_index: number;
  timestamp_ms: number;
  image_path: string;
  bbox_json: [number, number, number, number];
  detector_class: string;
  detector_confidence: number;
  blur_score: number | null;
  sharpness_score: number | null;
  area_ratio: number | null;
  flags_json: FrameCandidateFlags | null;
}

export interface TrackTimeline {
  track: Track;
  frames: FrameCandidate[];
}

/** One class this project can label with. Owned by the project and
 *  editable, so never cache it across projects. */
export interface ProjectClass {
  id: number;
  name: string;
}

/** A class as the editor sees it: the row, plus the id labels point at. */
export interface ClassDefinition {
  id: string;
  class_id: number;
  name: string;
  display_order: number;
}

/** How many labels a class is holding, asked before offering a delete. */
export interface ClassUsage {
  class_id: number;
  label_count: number;
}

/** How far through the frames the labelling has got. */
/** `rejected` is what a human set aside; `skipped` is what frame
 *  selection never offered. Counted apart because they say different
 *  things about the queue. */
export interface QueueProgress {
  pending: number;
  labeled: number;
  rejected: number;
  skipped: number;
  total: number;
}

/** What deleting a class did to the labels that were using it. */
export interface ClassDeleteOutcome {
  class_id: number;
  remapped: number;
  deleted_labels: number;
}

/** Starting points for a new project's class list. */
export const CLASS_PRESETS = [
  { id: "atcc-v1", label: "Traffic survey (20 vehicle classes)" },
  { id: "anpr-v1", label: "Number plates (vehicle + plate)" },
  { id: "blank", label: "Empty - I will define my own" },
] as const;

export type ReviewDecision = "accepted" | "hard" | "failed";

/** One box on one frame. Many per frame is the point. */
export interface Annotation {
  id: string;
  frame_id: string;
  /** Present only for labels written through track review. */
  frame_candidate_id: string | null;
  source: string;
  class_id: number | null;
  bbox_json: [number, number, number, number];
  attributes: Record<string, unknown>;
  status: string;
  updated_at: string;
}

/** One value a `choice` attribute can take. The value is stored, the
 *  label is read - `left_to_right` is a sane thing to query on and a
 *  poor thing to put in a dropdown. */
export interface AttributeOption {
  value: string;
  label: string;
}

/** One attribute a box can carry, and how to edit it.
 *
 *  Served by the backend rather than hard-coded here, because the same
 *  list is what the save is validated against: a copy on each side
 *  drifts, and the first sign of it is a control offering a value the
 *  server refuses. */
export interface AttributeDefinition {
  key: string;
  label: string;
  type: "text" | "choice" | "boolean";
  /** `choice` only; null for the other types, not absent. */
  options?: AttributeOption[] | null;
  /** `text` only; null for the other types, not absent. */
  max_length?: number | null;
  placeholder?: string | null;
}

export type FrameStatus = "pending" | "labeled" | "rejected" | "skipped";

/** A full frame, and its place in the labelling queue. */
export interface Frame {
  id: string;
  source_id: string;
  frame_index: number;
  timestamp_ms: number;
  width: number;
  height: number;
  status: FrameStatus;
  selection_reason: string | null;
}

/** A box as `[x1, y1, x2, y2]`. Full-frame pixels everywhere it is used. */
export type Bbox = [number, number, number, number];

/** One box as the canvas sends it. Coordinates are full-frame pixels.
 *  `id` is the annotation this box already is when it was loaded rather
 *  than drawn; echoing it keeps that row - and whatever indexes it -
 *  rather than replacing it with a new one. */
export interface FrameAnnotationWrite {
  id: string | null;
  class_id: number | null;
  bbox_json: Bbox;
  attributes: Record<string, unknown>;
}

export interface TrackReviewResult {
  track: Track;
  annotation: Annotation;
}

export interface DatasetVersion {
  id: string;
  project_id: string;
  version: number;
  created_at: string;
  split_seed: number;
  config_snapshot_json: Record<string, unknown>;
}

export interface DatasetExportResult {
  dataset_version: DatasetVersion;
  /** Images, per split plus `total`. One per exported frame. */
  counts: Record<string, number>;
  /** Boxes, per split plus `total`. A frame can hold many. */
  object_counts: Record<string, number>;
  /** Images exported with an empty label file, labelled as holding nothing.
   *  A subset of `counts.total`, not an addition to it. */
  background_frames: number;
  /** Exported frames carrying a box with no class; those boxes are not in
   *  the label file. */
  frames_with_unclassified_boxes: number;
  /** Labelled frames left out of the export entirely, because not one of
   *  their boxes had a class yet. */
  frames_skipped_unclassified: number;
  validation: { valid: boolean; errors: string[]; warnings: string[] };
}

export interface DetectionBox {
  track_id: string;
  tracker_track_id: number;
  /** Full source-frame pixels, [x1, y1, x2, y2]. */
  bbox: [number, number, number, number];
  detector_class: string;
  confidence: number;
  bucket: string | null;
  review_status: string;
}

export interface DetectionFrame {
  frame_index: number;
  timestamp_ms: number;
  boxes: DetectionBox[];
}

export interface DetectionRun {
  id: string;
  status: string;
  started_at: string;
  track_count: number;
}

export interface SourceDetections {
  source_id: string;
  width: number;
  height: number;
  fps: number;
  run_id: string | null;
  runs: DetectionRun[];
  frames: DetectionFrame[];
}

export interface RetrainingHandoffResult {
  data_yaml_path: string;
  instructions_path: string;
  data_yaml_content: string;
  instructions_content: string;
}

export interface QueueItem {
  track_id: string;
  review_status: ReviewStatus;
  bucket: TrackBucket | null;
  confidence: number;
  representative_frame_id: string | null;
}

export interface RtspStartResult {
  source: Source;
  run: ProcessingRun;
}

export interface RtspSessionStatus {
  run_id: string;
  connected: boolean;
  reconnect_attempts: number;
  frames_captured: number;
  frames_dropped: number;
  tracks_persisted: number;
  stopped: boolean;
  error: string | null;
}

/** One label worth a second look, and why.
 *
 *  `class_mismatch` - the detector saw something at that box and the
 *  human's class does not fit it. `missed_detection` - the human drew a
 *  vehicle the detector never found, so there is no detector class. */
export interface DisagreementItem {
  kind: "class_mismatch" | "missed_detection";
  frame_id: string;
  annotation_id: string;
  human_class_id: number;
  human_class_name: string;
  /** Null for a missed detection. */
  detector_class: string | null;
  /** Null unless the label was made by reviewing that track. A box drawn
   *  on the canvas belongs to a frame, so the frame is what to open. */
  track_id: string | null;
}

export interface ClassCount {
  class_id: number;
  name: string;
  box_count: number;
}

/** What a labeller has produced across a whole project.
 *
 *  A different question from the evaluation report's class distribution,
 *  which is scoped to one processing run and so cannot account for a box
 *  drawn on the canvas at all. */
export interface LabelBalance {
  classes: ClassCount[];
  total_boxes: number;
  unclassified_boxes: number;
  labeled_frames: number;
  background_frames: number;
}

export interface FailureGalleryItem {
  track_id: string;
  bucket: TrackBucket | null;
  review_status: ReviewStatus;
  representative_frame_id: string | null;
}

export interface EvaluationReport {
  run_id: string;
  source_id: string;
  is_frozen_validation_clip: boolean;
  ground_truth_vehicle_count: number | null;
  track_counts: { total: number; confirmed: number; failed: number; unreviewed: number };
  detection_recall: number | null;
  duplicate_track_pairs: [string, string][];
  fragmented_track_pairs: [string, string][];
  class_distribution: Record<string, number>;
  ocr_metrics: { tracks_with_ocr: number; agreements: number; corrections: number; agreement_rate: number | null };
  failure_gallery: FailureGalleryItem[];
}

export interface OcrCandidate {
  id: string;
  track_id: string;
  frame_candidate_id: string;
  source: string;
  plate_bbox_json: [number, number, number, number];
  text: string;
  normalized_text: string;
  confidence: number;
  selected: boolean;
  created_at: string;
}

export type JobType = "detect" | "select" | "train" | "export" | "preannotate";
export type JobStatus = "pending" | "running" | "succeeded" | "failed" | "cancelled";

/** A unit of work running outside the request that asked for it.
 *  `progress` is 0-1 and is merged server-side from the worker's live
 *  report while running, and from the job row once it has finished. */
export interface Job {
  id: string;
  project_id: string | null;
  type: JobType;
  status: JobStatus;
  progress: number;
  progress_message: string | null;
  result_json: Record<string, unknown> | null;
  error_message: string | null;
  pid: number | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

/** Processing now returns as soon as the work is queued; `run_id` is
 *  usable immediately, but the run has not necessarily started. */
export interface JobSubmitted {
  job: Job;
  /** Null for work that produces no processing run of its own, such as
   *  choosing which frames are worth labelling. */
  run_id: string | null;
}
