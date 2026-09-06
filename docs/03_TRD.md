# 03 --- Technical Requirements Document

## Technology

  Layer      Choice
  ---------- -------------------------------
  Desktop    Electron + React + TypeScript
  Backend    Python FastAPI
  ML         PyTorch / YOLO-family adapter
  Tracking   ByteTrack
  OCR        PaddleOCR
  Media      OpenCV + FFmpeg
  V1 DB      SQLite
  Testing    pytest + frontend tests

## Architectural Requirements

-   Detector, tracker, OCR and quality scoring are interfaces/adapters.
-   Business/domain logic must not directly depend on a specific YOLO
    model version.
-   Long processing jobs must not block API/UI.
-   Processing configuration is stored with each run.
-   Human annotations and model predictions are separate records.
-   DB changes use migrations.

## Performance

-   Avoid OCR on every frame.
-   Batch detection when hardware supports it.
-   Use bounded queues to prevent memory growth.
-   Generate thumbnails for reviewer UI.
-   Load full-resolution frames only when required.
-   Processing FPS is configurable and must be calibrated against real
    footage.

## Reliability

-   Processing job can fail gracefully without corrupting project state.
-   Reopening project restores review state.
-   Partial processing results are auditable.
-   Reprocessing cannot overwrite reviewed truth.

## Privacy

-   Local-first.
-   No automatic footage upload.
-   Credentials never written to logs.
-   Future RTSP secrets stored outside plain project JSON.
