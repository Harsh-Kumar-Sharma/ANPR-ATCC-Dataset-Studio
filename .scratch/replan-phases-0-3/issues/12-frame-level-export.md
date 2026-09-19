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
and the validator warns when they pass half the dataset, because a dataset
quietly made of nothing is a problem you want to see before you train on it.

**Establishing "background" took two goes, and the first one was wrong.** The
first version inferred it: frame `labeled`, no *accepted* boxes. Review found
three ways that inference is false, two of which turn a human's work into a
training image that contradicts it.

* A box reviewed `hard` is a human saying "this one is difficult". It is not an
  accepted label, so the frame had none - and the canvas marks a frame
  `labeled` on save while deliberately leaving a reviewed box's status alone.
  A frame holding a hard vehicle exported as a picture asserting the vehicle is
  not there.
* Deleting a class with its labels emptied frames without restating them, so a
  frame that held two vehicles a moment ago shipped as background.
* A frame with one classified box and one the user had not got to yet dropped
  the second box from the label file and reported nothing.

The rule is now positive rather than inferred: a background frame is one with
no annotation rows *at all*. Anything on the frame disqualifies it, whatever
that thing's status. And `delete_annotations` puts a frame back to `pending`
once its last human box is gone, which is the root fix for the second case and
is right independently of export - it already resets a track's review status
for exactly the same reason.

**The partial-label warning is suppressed only for a frame a human finished.**
"Finished" takes all three of: saved from the canvas, at least one box, and
every box carrying a class. The first separates "someone went through this
picture" from "someone reviewed one track that appears in it". The second and
third were added after review found the gate was `frame.status == "labeled"`
alone, which silently suppressed the warning on half-finished frames - the
exact case it exists for. A background frame is never finished by this
definition either: claiming a picture is empty over the detector's head is
worth saying out loud.

**The split, the decode pass and the version number now happen after the
export decides what it will write.** They used to run over everything the query
returned, including frames dropped later for having no classified box - so a
requested 80/10/10 over fifty frames could land as 82/8/10 over forty-five,
with five images decoded for nothing. An export with nothing left to write now
fails with `nothing_to_export` instead of creating an empty dataset version and
reporting that validation passed.

**The export response was counting boxes and calling them frames.** Found while
wiring this up: the API built its counts from `dataset_items`, which are one per
*box*, so fifty labelled frames holding seventy-five boxes came back as
seventy-five frames. Background frames would have been missed too, since they
write an image and no item rows - though that was a consequence waiting to
happen rather than one anyone hit, because before this ticket they were never
exported. The response now carries the manifest's own numbers, handed back by
the exporter rather than re-read from a file that holds an entry per box. The
desktop panel shows images, boxes, and what was left out.

**`_resolve_frame` deleted.** It created a `Frame` row on the fly for
pre-Phase-10 candidates that had no `frame_id`. Migration `2346228ab587` made
`annotations.frame_id` NOT NULL and backfilled it, so the case it existed for
can no longer reach the database.

**Evaluation's class distribution still joins through the candidate, and that is
correct.** It is scoped to a processing *run*, and a canvas box belongs to a
frame, which belongs to a source that may have several runs - there is no honest
answer to which run produced it.

**Active learning's disagreement queue is a different story, and my first note
about it was wrong.** I claimed it needs a detection to compare against and so
is correctly candidate-scoped. It is not: the frames canvas boxes sit on *do*
carry candidate rows with their own boxes, so a canvas label could be matched
to a detection by overlap. The join is a gap, not a law. A project labelled
entirely on the canvas gets an empty queue that reads as "no problems found".
The code now says that plainly, and it is ticket 14.

**One claim in the commit message overstates the case** and is corrected here
rather than by rewriting history: it says a background frame "reported as none"
under the old per-box counting. True of the counting, but nobody could have hit
it, because the old exporter skipped every frame with no label lines. The
per-box bug was real; that particular consequence was not yet reachable.

**Two smaller things review caught.** The counts test used two, one and zero
boxes across three frames, so images and boxes both totalled three and the
assertion could not tell them apart - it now uses two, two and zero. And
`_tracks_by_annotation` had its own copy of the chunking helper that already
exists in `annotations.py`.
