# 06 --- UI/UX Specification

## Screen 1 --- Projects

-   Create/open project.
-   Recent projects.
-   Project statistics.
-   Current dataset/model state.

## Screen 2 --- Source Import

-   Select video.
-   Show FPS, resolution, duration.
-   Select processing profile.
-   Select class schema.
-   Start processing.

## Screen 3 --- Processing

-   Progress.
-   Current source timestamp.
-   Processing FPS.
-   Track count.
-   Queue/job state.
-   Pause/cancel.
-   Warnings/errors.

## Screen 4 --- Track Review

This is the main labeling screen.

### Layout

-   Large selected frame.
-   Vehicle bounding box.
-   Track timeline/filmstrip.
-   Alternate candidate frames.
-   Vehicle class selector.
-   OCR candidate panel.
-   Quality metrics.
-   Bucket/status.
-   Next/previous controls.

### Required Actions

-   Accept.
-   Change class.
-   Edit bounding box.
-   Mark hard.
-   Mark failed/needs review.
-   Select alternate best frame.
-   Correct OCR.

### Keyboard Workflow

Support configurable shortcuts for: - next/previous track; - accept; -
hard; - failed; - class selection; - box-edit mode.

## Screen 5 --- Dataset

-   Reviewed/unreviewed counts.
-   Class distribution.
-   Hard/failed counts.
-   Dataset version builder.
-   Export.

## Screen 6 --- Evaluation

-   Detection metrics.
-   ATCC confusion.
-   OCR metrics.
-   Failure examples.

## UX Principle

Review the **vehicle track**, not only an isolated image. A reviewer
must be able to move through alternate frames without losing the vehicle
identity.
