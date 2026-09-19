# 12: Export labeled frames as a valid multi-box YOLO dataset

**What to build:** Work done in the labeling canvas travels all the way out into a training-ready YOLO dataset. This is the seam that makes the labeling subsystem *connected* rather than merely complete, and it is Phase 3's done-when.

The export path currently queries through annotation to frame candidate to track to run to source, which is track-keyed and cannot express several objects on one image. Frame grouping already exists inside the exporter; the queries underneath it are what change.

**Blocked by:** 08 (Draw one box on a frame and have it persist), 09 (Label a frame fully, keyboard-first)

**Status:** done

- [x] The dataset query is frame-level rather than track-keyed
- [x] Export emits one label file per image containing every box on that frame
- [x] Rejected frames and unlabeled frames are excluded
- [x] Fifty frames labeled in the canvas export as a valid YOLO dataset and the existing dataset validator passes it
- [x] The existing split logic still applies and is not bypassed

**`query_approved_items` is gone, replaced by `query_export_frames`.** The old
query joined annotation to candidate to track to run to source, so a box with no
candidate did not exist as far as export was concerned - an afternoon on the
canvas exported as `nothing_to_export`. The new one joins `Annotation.frame_id`
to the frame and the frame to its source, which is the same question asked of
the thing that actually holds the box. Both labelling paths come back from it,
which the mixed-labelling test pins down.

**A frame a human labelled as empty is exported as a background image.** Saving
zero boxes is a real label - it is the negative example a detector needs - and
dropping it would have made ticket 9's "zero boxes is a real label" true only
inside the canvas. Counted separately in the manifest as `background_frames`,
because a dataset that is quietly mostly background is a problem you want to be
able to see. A frame whose only boxes are *unclassified* is not background: that
is unfinished work, and shipping it would teach the model to ignore the very
vehicles someone was part-way through labelling.

**The partial-label warning no longer fires on canvas-labelled frames.** It means
"the detector found a vehicle nobody accepted", which was a fair inference while
the only way to label was to review one track at a time. On a frame a human went
through box by box, a detection without a box is one they *declined* - warning
about it would be telling them off for doing the job. The heuristic now applies
only to frames still at `pending`.

**The export response was counting boxes and calling them frames.** Found while
wiring this up: the API built its counts from `dataset_items`, which are one per
*box*, so fifty labelled frames holding seventy-five boxes came back as
seventy-five frames - and a background frame, which writes an image and no items,
came back as none. The response now reports the manifest the export just wrote,
and carries images and boxes as the two different numbers they are. The desktop
panel shows both.

**`_resolve_frame` deleted.** It created a `Frame` row on the fly for
pre-Phase-10 candidates that had no `frame_id`. Migration `2346228ab587` made
`annotations.frame_id` NOT NULL and backfilled it, so the case it existed for
can no longer reach the database.

**Evaluation's class distribution still joins through the candidate, and that is
correct.** It is scoped to a processing *run*, and a canvas box belongs to a
frame, which belongs to a source that may have several runs - there is no honest
answer to which run produced it. Same for active learning's disagreement queue,
which compares a human's class against a *detector's* prediction and so needs a
detection to compare with. Both now say so in the code rather than looking like
oversights.
