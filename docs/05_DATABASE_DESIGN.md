# 05 --- Database & Storage Design

## Core Tables

### projects

`id, name, created_at, class_schema_version, workspace_path`

### sources

`id, project_id, type, path_or_uri, fps, width, height, duration`

### processing_runs

`id, source_id, detector_version, tracker_config, sampling_config, started_at, completed_at, status`

### tracks

`id, run_id, tracker_track_id, start_ts, end_ts, bucket, review_status`

### frame_candidates

`id, track_id, timestamp_ms, image_path, bbox_json, detector_class, detector_confidence, blur_score, sharpness_score, area_ratio, flags_json`

### ocr_candidates

`id, track_id, frame_candidate_id, plate_bbox_json, text, normalized_text, confidence`

### annotations

`id, frame_candidate_id, source, class_id, bbox_json, status, updated_at`

`source` must distinguish at least `model` and `human`.

### dataset_versions

`id, project_id, version, created_at, split_seed, config_snapshot_json`

### dataset_items

`dataset_version_id, annotation_id, split, export_path`

### audit_events

`id, entity_type, entity_id, action, payload_json, created_at`

## Workspace

``` text
workspace/<project_id>/
  source/
  cache/thumbnails/
  derived/tracks/<track_id>/
  derived/plates/
  review/
  exports/<dataset_version>/
  logs/
  project.json
```

## Data Rules

-   Automated quality logic cannot physically delete raw/track evidence.
-   Human edits are authoritative.
-   Every derived artifact stores enough lineage to find its source.
-   Dataset versions are immutable after finalization.
