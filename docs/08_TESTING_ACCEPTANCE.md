# 08 --- Testing & Acceptance

## Unit Tests

-   Bounding-box conversion.
-   Class mapping.
-   Quality-score functions.
-   OCR normalization.
-   Dataset split.
-   Manifest generation.

## Adapter Tests

Recorded fixtures for: - detector; - tracker; - OCR.

## Pipeline Tests

A deterministic short clip must verify: - timestamps; - track
creation; - candidate persistence; - hard/failed preservation; -
processing completion.

## UI Tests

-   Edits persist.
-   Keyboard shortcuts do not lose unsaved work.
-   Human labels remain after reopen/re-inference.

## Export Tests

-   Image exists for every exported label.
-   Label format valid.
-   Class IDs valid.
-   Manifest traceability valid.
-   Split deterministic for fixed seed/version.

## Core Metrics

-   Vehicle detection recall.
-   Duplicate-track rate.
-   Track fragmentation rate.
-   ATCC per-class precision/recall.
-   Confusion matrix.
-   OCR exact/normalized match.
-   Reviewer correction rate.
-   Hard/failed preservation rate.

## V1 Release Gate

-   No known automatic data-loss path.
-   Project reopen restores review state.
-   Export validator passes.
-   At least one real representative gantry clip completes end-to-end.
