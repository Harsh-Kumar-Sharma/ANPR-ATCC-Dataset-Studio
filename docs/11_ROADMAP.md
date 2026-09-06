# 11 --- Product Roadmap

## V1

-   Offline video.
-   Detection.
-   Tracking.
-   Smart frame selection.
-   Track review.
-   OCR.
-   Dataset version/export.

## V1.5

-   Training launcher.
-   Experiment registry.
-   Evaluation dashboard.
-   Active-learning queue.

## V2

-   RTSP live capture.
-   Rolling buffer.
-   Multi-camera project.
-   Continuous hard/failure harvesting.

## V2.5

-   Cross-camera metadata.
-   Central model registry.
-   Optional PostgreSQL.

## V3

-   Team review.
-   Roles/permissions.
-   Remote workers.
-   Object storage.
-   External APIs.

## Long-Term Invariant

All future stages must preserve:
`source -> track -> evidence -> annotation -> dataset version -> model/evaluation`
lineage.
