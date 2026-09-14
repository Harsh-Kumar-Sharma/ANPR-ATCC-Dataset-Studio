# Architecture / Product Decisions

Use this file as an append-only decision log.

## D-001 --- Track Is Primary Review Unit

**Status:** Accepted\
A vehicle track, rather than an isolated frame, is the main unit for
deduplication and review.

## D-002 --- Preserve Difficult Data

**Status:** Accepted\
Blurred, low-confidence, OCR-failed and otherwise difficult vehicles are
retained as hard/failed evidence instead of automatically deleted.

## D-003 --- Separate Human and Model Labels

**Status:** Accepted\
Model predictions and human-reviewed annotations are stored separately.
Human truth wins.

## D-004 --- Different Best Frames

**Status:** Accepted\
The best ATCC/detection frame and the best OCR frame may differ within
the same vehicle track.

## D-005 --- Local-First V1

**Status:** Accepted\
V1 operates locally with SQLite and local workspace storage.

## D-006 --- Pluggable ML Engines

**Status:** Accepted\
YOLO, ByteTrack and PaddleOCR are initial implementations behind
interfaces and may be replaced after benchmarking.

## D-007 --- Training Data Is Full Frames, Not Crops

**Date:** 2026-09-11\
**Status:** Accepted\
**Context:** Phases 2-6 stored only per-detection crops and exported one
cropped image per track whose single box filled nearly the whole image.
That is fine for review, but a detector trained on it learns that a
vehicle always fills the frame and fails on real gantry footage where a
vehicle covers a few percent of the pixels.\
**Decision:** Dataset exports emit full source frames with every accepted
box for that frame in one label file, and the train/val/test split unit
is the frame rather than the track.\
**Reason:** Two vehicles can share one frame; splitting by track would
put the same pixels in two splits, each missing the other's box - both
leakage and wrong labels. The review UI keeps using crops, unchanged.\
**Consequences:** `frames` table added; `frame_candidates.frame_id`
links a crop to its parent frame; the export manifest is frame-shaped
(`items[].objects[]`) rather than annotation-shaped.

## D-008 --- Full Frames Are Recovered On Demand, Not Stored Eagerly

**Date:** 2026-09-11\
**Status:** Accepted\
**Context:** Full-frame export needs the original pixels. Persisting
every sampled frame costs roughly 160-310 MB per 156-second clip at
5 fps/1080p, most of it for frames that are never labeled or exported.\
**Decision:** `frames` rows record identity only (`source_id`,
`frame_index`, `timestamp_ms`, size) with a nullable `image_path`.
Pixels are decoded from the retained source video on first use and
cached to `derived/frames/`.\
**Reason:** The imported source video is copied into the workspace and
never deleted, and `frame_index` identifies a frame exactly, so the
pixels are always recoverable. Storage is spent only on frames that are
actually used.\
**Consequences:** Export depends on the source file still being present
and raises a clear error if it is not. A recorded `image_path` that no
longer exists is treated as "not materialized" and re-decoded rather
than failing. Frames captured from **RTSP have no re-decodable source**
and are therefore not covered by this scheme - live-capture frames
remain crop-only until a follow-up persists them eagerly.

------------------------------------------------------------------------

## New Decision Template

### D-XXX --- Title

**Date:** YYYY-MM-DD\
**Status:** Proposed / Accepted / Superseded\
**Context:**\
**Decision:**\
**Reason:**\
**Consequences:**
