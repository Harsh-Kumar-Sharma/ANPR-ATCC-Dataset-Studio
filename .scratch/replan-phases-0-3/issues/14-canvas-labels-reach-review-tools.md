# 14: Make canvas labels visible to the tools that review labelling

**What to build:** A user who labels entirely in the canvas can see the class
balance of their own work and can be told when their class disagrees with the
detector's. Today both screens answer "nothing", and "nothing" is
indistinguishable from "nothing wrong".

Found during the review of ticket 12, not by a user. Ticket 12 made canvas boxes
reach the dataset; it did not make them reach the two screens that exist to tell
a labeller whether their labelling is any good.

**Blocked by:** 12 (Export labeled frames as a valid multi-box YOLO dataset)

**Status:** done

Two separate problems that happen to have the same cause.

**Active learning's disagreement queue** (`find_model_human_disagreements`) joins
`Annotation.frame_candidate_id`, so it sees only labels written by track review.
A canvas box has no candidate. This one is a genuine gap rather than a limit: the
frames those boxes sit on do carry `FrameCandidate` rows with their own boxes, so
a canvas label can be matched to a detection by overlap and compared the same
way. It needs an overlap threshold chosen deliberately, and a decision about what
to do when a canvas box matches no detection at all.

**Evaluation's class distribution** (`_compute_class_distribution`) is scoped to a
processing *run*, and a canvas box belongs to a frame, which belongs to a source
that may have several runs. There is no honest way to attribute it to a run, so
this is not the same fix. What a canvas labeller actually needs is a class
distribution over their *labelled frames*, which is a different question from the
one the evaluation report asks. Answer it separately rather than bending the
run-scoped one.

- [x] A canvas-drawn box whose class contradicts the detector's class on an overlapping detection appears in the disagreement queue
- [x] The overlap rule is chosen explicitly and stated, not left implicit in a magic number
- [x] A canvas box matching no detection is handled deliberately, and the choice is recorded
- [x] A labeller can see the class balance of their own labelled frames, whatever path the labels were written by
- [x] Where a screen genuinely cannot answer for canvas labels, it says so rather than rendering "None"

**A box nothing matches is reported, not dropped.** `unmatched_box` is its own
kind alongside `class_mismatch`. There is nothing to disagree with, which is
what makes it the most useful entry in the queue: a class mismatch might be a
labelling question, while a vehicle the detector never found is most likely the
model's own miss.

**Both the threshold and the name took two goes.** I first used 0.5 and argued
it was right because detection benchmarks have used it for "correct" since
PASCAL VOC. Review took that apart and was right to:

* Scoring a detection and *associating* one with a human's box are different
  questions, and the first is the stricter. This codebase already uses 0.3 for
  the same "is this the same vehicle" question, in `track_metrics`.
* My supporting claim - that 0.5 is "what every mAP figure this model gets
  compared against means by one" - was simply false. The detector here is
  COCO-pretrained, and COCO's headline mAP averages IoU 0.50 to 0.95.
* The consequence was not benign. A concentric box at 70% of each side overlaps
  49%, which is an ordinary tightening of a loose truck box - and it was then
  reported to the user as a vehicle the detector had missed. I had called that
  "the direction to fail in". It is not: the entry was not merely routed to a
  person, it was routed with a false accusation against the model attached.

The threshold is 0.3 now, matching the codebase's other association threshold
and carrying the same "uncalibrated, not tuned against real footage" caveat as
every other number here. And the kind is named for what is actually known -
nothing the detector found matches this box - rather than for the likeliest
explanation.

**Every matching detection is asked, not the best-overlapping one.** Two
vehicles almost on top of each other is the ordinary dense case on gantry
footage. Picking the single highest-overlap detection flagged a correct label as
wrong whenever the other one was the one the human meant, and with two equal
overlaps the tie broke on database row order - so the answer was not even
stable. If any matching detection allows for the human's class, there is nothing
to report. The candidate query is ordered now too.

**The queue is capped**, like the two queues either side of it. Every canvas box
the detector missed is an entry, which on real footage is thousands, rendered
into one list with no paging.

**Only a label written by reviewing a track carries a track id.** A canvas box
matched to a detection by overlap is not *about* that detection's track -
opening the track would not even show the box - so the field keeps meaning one
thing and the panel offers the frame instead. Every entry carries a frame id,
because every box is on a frame however it was written.

**Geometry is the last resort, not the method.** A label from track review names
its candidate outright, and that link is the truth; re-deriving it from overlap
could only make it worse. Guessing is confined to the case that has nothing
better.

**The balance counted only canvas-saved frames, and that was live on real
data.** `Frame.status` is set to `labeled` only by a canvas save - track review
never touches it - so a project reviewed track by track reported zero labelled
frames next to a non-zero box count. The user's own database is exactly that
shape: 4,029 frames, all `pending`, 20 carrying human labels. A frame counts as
worked on now if it carries a human box *or* was saved from the canvas, counted
distinctly so a frame done both ways is not counted twice. Background frames
follow the export's stronger rule - no annotation rows whatsoever - rather than
the weaker "no human rows", which is the rule that let a hard-reviewed vehicle
become a background image in ticket 12.

**The class balance is a new question, not a fix to the old one.** Evaluation's
distribution stays scoped to a processing run, because that is the right unit
for asking how a detection pass went and a canvas box genuinely does not belong
to one. `GET /projects/{id}/label-balance` asks the project-level version
instead - every human label a dataset could contain, whichever way it was
written - and the two coexist because they are different questions. The run
report's empty state now says which one it is answering rather than "None yet.",
which a canvas labeller could only read as "you have labelled nothing".

**The balance counts what a labeller needs to act on**, not just what exports:
background frames (the part of a dataset's balance most easily got wrong by
accident) and boxes still missing a class (the difference between "my dataset is
small" and "my dataset is small because forty boxes need a class"). `hard`
labels are excluded - a hard decision is explicitly unsettled, and belongs in
the active-learning queue rather than in a number read as yield.

**Two of my own test expectations were wrong** and are worth recording. I
asserted a canvas box would have no track even when it matched a detection -
true only after I decided it should be, which the test then pinned. And a
balance test picked its "other" frame using a field the track timeline does not
serve, so it silently picked the reviewed frame and the whole-set replacement
deleted the label under test.

**One more claim of mine was overstated.** I wrote that evaluation's
distribution "genuinely cannot" account for canvas boxes. There is no recorded
link from a frame to a run, but the frame's detections do name one, and this
same ticket is willing to bind a canvas box to a detection by overlap. The
honest version is that it would have to guess, and would still have nothing for
an unmatched box - so the distribution stays run-scoped by choice, not by
impossibility.

**Two empty states still lied, in the way this ticket was written to stop.**
"Nothing to flag - every label agrees with the model" is a clean bill of health,
and it was shown to a project with no labels at all. The panel now asks the
balance and tells the two apart. And the evaluation report's "No track reviewed
in this run" was false when every track had been reviewed and failed, since only
accepted and hard reviews carry a class.
