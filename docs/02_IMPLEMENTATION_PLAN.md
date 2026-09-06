# 02 --- Implementation Plan

## Phase 0 --- Bootstrap

### Build

-   Repository structure.
-   FastAPI backend skeleton.
-   Electron + React + TypeScript desktop skeleton.
-   SQLite initialization and migrations.
-   Config/logging/error handling.
-   Test commands.

### Definition of Done

Desktop and backend start locally, health check works, DB initializes,
and baseline tests pass.

## Phase 1 --- Project + Video Ingestion

### Build

-   Project CRUD.
-   Workspace creation.
-   Video import.
-   Source metadata.
-   Frame decoder.
-   Timestamp mapping.
-   Processing profile.

### Definition of Done

A source video can be imported and sampled with reproducible source
timestamps.

## Phase 2 --- Detection + Tracking

### Build

-   Detector interface.
-   YOLO adapter.
-   Tracker interface.
-   ByteTrack adapter.
-   Track persistence.
-   Track timeline API.

### Definition of Done

Vehicles are represented as persistent tracks with candidate frames.

## Phase 3 --- Smart Frame Selection

### Build

-   Sharpness/blur signals.
-   Bounding-box area/visibility.
-   Boundary truncation.
-   Temporal confidence stability.
-   Representative-frame ranking.
-   Hard/failed classification.

### Definition of Done

Each closed track gets ranked `BEST_DETECTION`, OCR candidates, and
hard/failed evidence without deleting track history.

## Phase 4 --- Review UI

### Build

-   Track browser.
-   Frame timeline.
-   Bounding-box editor.
-   Class editor.
-   Accept/hard/failed actions.
-   Keyboard shortcuts.
-   Review persistence.

### Definition of Done

Core labeling can be completed without CVAT for the supported vehicle
workflow.

## Phase 5 --- OCR

### Build

-   Plate candidate interface.
-   PaddleOCR adapter.
-   Multi-frame OCR attempts.
-   Normalization.
-   Confidence history.
-   Track-level selected OCR.

### Definition of Done

OCR failure on the first candidate triggers alternate suitable frames
and all candidate results remain traceable.

## Phase 6 --- Dataset Version + Export

### Build

-   Approved-item query.
-   Train/val/test split.
-   YOLO image/label writer.
-   Class schema export.
-   Manifest.
-   Integrity validator.

### Definition of Done

A reproducible, validated dataset version can be exported.

## Phase 7 --- Evaluation

-   Frozen validation clips.
-   Detection recall.
-   Duplicate/fragmentation metrics.
-   ATCC per-class metrics.
-   OCR metrics.
-   Failure gallery.

## Phase 8 --- Active Learning

-   Low-confidence queue.
-   Hard/failed queue.
-   Model disagreement.
-   Retraining handoff.

## Phase 9 --- RTSP

-   RTSP source adapter.
-   Bounded rolling buffer.
-   Reconnect behavior.
-   Same downstream track/review contracts.

## Build Order Rule

Codex must complete the earliest incomplete phase and its tests before
moving forward unless explicitly instructed otherwise.
