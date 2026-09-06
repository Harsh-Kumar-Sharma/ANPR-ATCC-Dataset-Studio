# 01 --- Product Requirements Document

## 1. Product Vision

Build a local desktop application that converts offline gantry videos,
and later RTSP streams, into traceable ANPR/ATCC training data with much
less manual labeling.

The system combines: - vehicle detection; - vehicle tracking and
deduplication; - frame quality analysis; - smart representative-frame
selection; - OCR candidate analysis; - model-assisted labels; - human
review; - dataset versioning and export; - active learning.

## 2. Primary Users

### ML/CV Engineer

Creates datasets, analyzes failure cases, trains models and evaluates
model versions.

### Labeling Reviewer

Approves or corrects boxes/classes quickly using a track-based review
interface.

### QA / Project Engineer

Measures data/model quality and traces exported samples back to their
source.

## 3. Problems Being Solved

1.  Manual extraction and labeling of video frames is slow.
2.  Consecutive frames create large numbers of duplicate vehicle
    samples.
3.  Fast vehicles can be blurred or missed.
4.  Poor images are often incorrectly removed even though they are
    valuable hard examples.
5.  ANPR and ATCC may require different best frames from the same
    vehicle.
6.  OCR may fail on one frame while succeeding on another frame in the
    same track.

## 4. Non-Negotiable Invariants

-   Never silently delete a vehicle because quality is poor.
-   Preserve source → timestamp → track → frame → annotation lineage.
-   Deduplicate primarily at vehicle-track level.
-   Preserve alternate frames within a vehicle track.
-   Human-reviewed annotations override model suggestions.
-   Best ATCC/detection frame and best OCR frame may be different.
-   OCR failure does not mean vehicle failure.

## 5. V1 Goals

-   Project creation/opening.
-   Video import.
-   Configurable frame processing.
-   Vehicle detection.
-   ByteTrack-based tracking.
-   Per-track candidate-frame history.
-   Smart frame scoring.
-   `BEST_DETECTION`, `BEST_OCR`, `HARD`, `FAILED` buckets.
-   Auto-label suggestions.
-   Human review/correction.
-   PaddleOCR integration.
-   YOLO-compatible dataset export.
-   Dataset version manifest.
-   Basic statistics.

## 6. V1 Non-Goals

-   Multi-tenant cloud SaaS.
-   Distributed GPU workers.
-   Toll transaction processing.
-   Fully autonomous no-review labeling.
-   Generic replacement for every CVAT annotation workflow.

## 7. Functional Requirements

  ID      Requirement                                    Priority
  ------- ---------------------------------------------- ----------
  FR-01   Create/open project                            P0
  FR-02   Import MP4/AVI/MKV source                      P0
  FR-03   Preserve exact source timestamps               P0
  FR-04   Detect vehicles                                P0
  FR-05   Assign stable track IDs                        P0
  FR-06   Preserve frame candidates per track            P0
  FR-07   Rank best/hard/failed candidates               P0
  FR-08   Suggest vehicle boxes/classes                  P0
  FR-09   Human accept/edit/reclassify                   P0
  FR-10   Try alternate track frames after OCR failure   P1
  FR-11   Export YOLO dataset + manifest                 P0
  FR-12   Dataset statistics dashboard                   P1
  FR-13   RTSP ingestion                                 P2
  FR-14   Active-learning queue                          P2

## 8. Default ATCC Classes

1.  Two Wheeler
2.  Three-Wheeler Passenger
3.  Three-Wheeler Freight
4.  Car/Jeep/Van
5.  LCV 2-Axle
6.  LCV 3-Axle
7.  Bus 2-Axle
8.  Bus 3-Axle
9.  Mini-Bus
10. Truck 2-Axle
11. Truck 3-Axle
12. Truck 4-Axle
13. Truck 5-Axle
14. Truck 6-Axle
15. Truck Multi-Axle (7+)
16. Earth Moving Machinery
17. Heavy Construction Machinery
18. Tractor
19. Tractor with Trailer
20. Tata Ace / similar mini LCV

Class configuration must be versioned and project-configurable.

## 9. Acceptance Criteria

-   Every exported item is traceable to source video, timestamp and
    track ID.
-   Low-quality tracks remain visible for review.
-   Duplicate frames from one vehicle are not exported by default as
    independent samples.
-   Human edits survive re-inference.
-   Fixed dataset version/config produces deterministic export.
