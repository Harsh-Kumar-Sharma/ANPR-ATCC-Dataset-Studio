# Development Handoff

This file is updated by Codex after every meaningful implementation
session.

## Current Phase

Phase 2 --- Detection + Tracking (complete)

## Completed This Session

-   **Detector interface** (`app/ml/detector.py`): a `Detector`
    protocol (`model_version`, `class_names`, `detect(frame)`), so
    business logic never depends on a specific model version per
    `docs/03_TRD.md`.
-   **YOLO adapter** (`app/ml/yolo_detector.py`): wraps Ultralytics
    YOLO. Default weights are `yolo26n.pt` (per user direction),
    COCO-pretrained, filtered to a vehicle-relevant class allowlist
    (bicycle/car/motorcycle/bus/truck). Fine-grained ATCC
    classification is out of scope for this adapter - it's a human
    review + future custom-trained-model concern, not this phase's.
    ANPR plate detection is intentionally **not** part of this
    detector (confirmed with the user): plates are only ever detected
    on an already-tracked vehicle crop, which is Phase 5 (OCR) scope.
-   **Tracker interface** (`app/ml/tracker.py`) +
    **ByteTrack adapter** (`app/ml/bytetrack_tracker.py`): wraps the
    `trackers` package's `ByteTrackTracker` (the actively maintained
    successor to `supervision.ByteTrack`, which is deprecated and
    slated for removal in supervision 0.31). A track only gets a
    persistent id after 2 consecutive consistent detections (the
    library's own activation logic) - single-frame noise never
    becomes a track.
-   **Track persistence** (`app/services/track_processor.py`): decodes
    every sampled frame, runs detector -> tracker, and for each
    resulting track writes a `tracks` row plus one `frame_candidates`
    row per observation, with the vehicle crop saved to
    `workspace/<project>/derived/tracks/<track_id>/frame_NNNNNN.jpg`.
    `tracks`/`frame_candidates` tables added via Alembic migration
    `982683a882e0`, matching `docs/05_DATABASE_DESIGN.md` (quality
    columns - `blur_score`/`sharpness_score`/`area_ratio`/`flags_json`
    - stay null; that's Phase 3).
-   **Track timeline API**: `GET /projects/{id}/tracks` (list, filter
    by `run_id`) and `GET /tracks/{id}` (track + its frame candidates
    ordered by `frame_index` - what Phase 4's review UI will browse).
-   New endpoint `POST /projects/{id}/sources/{id}/process`: runs the
    full detect+track pipeline over a source (separate from Phase 1's
    `/sample`, which only checks timestamp sampling). Detector is
    injected via FastAPI `Depends`, so API tests substitute a
    deterministic stub instead of loading real YOLO weights.
-   Backend dependencies added: `ultralytics` (pulls `torch`/
    `torchvision`, confirmed compatible with this machine's Python
    3.14 - `torch==2.14.0+cpu`), `trackers`. Model weights cache at
    `backend/data/models/` (gitignored via the existing `backend/data/`
    rule).

## Files Changed

-   Added `backend/app/ml/{__init__,types,detector,yolo_detector,tracker,bytetrack_tracker,factory}.py`.
-   Added `backend/app/db/models/{track,frame_candidate}.py`.
-   Added `backend/app/services/track_processor.py`.
-   Added `backend/app/schemas/track.py`; extended
    `backend/app/schemas/processing_run.py` with `detector_version`/
    `tracker_config`.
-   Added `backend/app/api/tracks.py`; extended
    `backend/app/api/sources.py` with `/process`.
-   Extended `backend/app/core/config.py` with `model_weights_dir`.
-   Added Alembic migration `982683a882e0`.
-   Added tests: `services/test_bytetrack_tracker.py`,
    `services/test_yolo_detector.py`, `services/test_track_processor.py`,
    `test_tracks_api.py`, `tests/stub_detector.py`.

## Tests / Commands Run

-   `alembic revision --autogenerate` + `alembic upgrade head` (clean).
-   `pytest`: 29 passed (includes real YOLO model load/inference and
    real ByteTrack activation behavior, not just mocks).
-   Live smoke test via `uvicorn` + `curl` using the **real** detector
    (no stub): created a project, imported a 40-frame/20fps synthetic
    video, called `/process` - completed in ~1.9s, correctly returned
    zero tracks (the synthetic clip has no real vehicle pixels for
    YOLO to find), `detector_version` recorded as the resolved weights
    path. No errors anywhere in the real detector -> tracker ->
    persistence path. Dev DB/workspace cleaned up afterward; model
    weights cache left in place intentionally (avoids re-download).

## Known Issues / Assumptions

-   **No real gantry footage tested yet.** Everything above is
    verified against synthetic clips (solid-color frames) because none
    is available. The pipeline's mechanics are proven; detection
    quality/recall on actual vehicles is not - unchanged known issue,
    now more pressing since Phase 2 is the phase that needed it.
-   `/process` still runs synchronously in the request handler, same
    as Phase 1's `/sample`. Now more urgent than when first flagged,
    since real footage will take meaningfully longer than the ~2s seen
    on a 40-frame test clip. Flagged as a background task
    (`spawn_task`) for a focused follow-up rather than bolted on here.
-   `yolo26n.pt` is a pretrained COCO checkpoint, not a model trained
    on the 20 ATCC classes in `docs/01_PRD.md`. It only proves the
    detect -> track -> persist mechanics; ATCC-specific accuracy
    requires either fine-tuning or accepting COCO class names until a
    custom model is trained (this project's own eventual output).
-   ByteTrack's `minimum_consecutive_frames=2` default means a
    single-frame detection never becomes a track (by design - avoids
    one-frame noise), but also means a vehicle only visible in exactly
    one sampled frame produces zero track/frame_candidates rows. Worth
    watching against the "never silently lose a vehicle" invariant
    once real footage is available; may need a lower sampling interval
    or a different activation policy. Not addressed here since it
    can't be evaluated without real footage.
-   `docs/05_DATABASE_DESIGN.md`'s `frame_candidates` doesn't list a
    `frame_index` column (only `timestamp_ms`); added it anyway since
    the sampler/decoder already key everything by frame index and
    losing that would make crops harder to trace back. Minor, additive
    deviation, same spirit as Phase 1's `duration_ms`/`frame_count`
    additions to `sources`.
-   `npm audit` finding from Phase 0 in `desktop/` transitive dev deps
    still unaddressed (unchanged).

## Data-Preservation Checks

-   Every activated tracked detection becomes a `frame_candidates` row
    with its crop saved to disk - nothing is scored, ranked, or
    dropped as "low quality" in this phase (that's Phase 3). The only
    thing not persisted is a geometrically-degenerate bbox (zero
    width/height after clamping to frame bounds), which is a data
    anomaly, not a quality judgment call.
-   Re-running `/process` on the same source creates a new
    `processing_runs` row and a fresh set of tracks under it; it never
    touches a prior run's tracks/frame_candidates, so nothing already
    persisted can be overwritten by reprocessing.

## Next Exact Task

Begin Phase 3 --- Smart Frame Selection per
`docs/02_IMPLEMENTATION_PLAN.md`: sharpness/blur signals, bounding-box
area/visibility, boundary truncation, temporal confidence stability,
representative-frame ranking, hard/failed classification. This
populates the `blur_score`/`sharpness_score`/`area_ratio`/`flags_json`
columns already sitting null on `frame_candidates`, and sets
`tracks.bucket` (`BEST_DETECTION`/`HARD`/`FAILED`) for the first time.

## Do Not Redo

-   Do not redesign the product requirements unless a real
    implementation constraint requires a documented decision.
-   Do not replace the chosen stack without explicit approval; the
    detector/tracker choices this session (`yolo26n.pt`, `trackers`
    package's ByteTrack) were a direct user decision, not a default -
    don't silently swap them.
-   Do not re-scaffold `backend/` or `desktop/` from scratch; extend
    what exists.
-   Do not re-derive frame timestamps from wall-clock/decode timing;
    `frame_sampler.py`'s frame-index-based approach is what makes
    sampling reproducible.
-   Do not use `supervision.ByteTrack` directly - it's deprecated;
    use the `trackers` package's `ByteTrackTracker` via
    `app/ml/bytetrack_tracker.py`.
