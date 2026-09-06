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

------------------------------------------------------------------------

## New Decision Template

### D-XXX --- Title

**Date:** YYYY-MM-DD\
**Status:** Proposed / Accepted / Superseded\
**Context:**\
**Decision:**\
**Reason:**\
**Consequences:**
