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

**A box matching no detection is reported, not dropped.** `missed_detection` is
its own kind alongside `class_mismatch`. There is nothing to disagree with,
which is exactly what makes it the most useful entry in the queue: a class
mismatch might be a labelling question, but a vehicle the human drew and the
detector never found is unambiguously the model's miss.

**The overlap rule is 0.5, and it is 0.5 for a reason.** That is the threshold
detection benchmarks have used for "correct" since PASCAL VOC, so a match here
means what every mAP number this model gets compared against means by one. The
failure mode is stated in the code rather than discovered later: a human who
redraws a sloppy detection much tighter can fall below it, and their box is then
reported as a miss rather than a class disagreement. That is the direction to
fail in - it sends the case to a person instead of quietly pairing two boxes
that may be different objects.

**Only a label written by reviewing a track carries a track id.** A canvas box
matched to a detection by overlap is not *about* that detection's track -
opening the track would not even show the box - so the field keeps meaning one
thing and the panel offers the frame instead. Every entry carries a frame id,
because every box is on a frame however it was written.

**Geometry is the last resort, not the method.** A label from track review names
its candidate outright, and that link is the truth; re-deriving it from overlap
could only make it worse. Guessing is confined to the case that has nothing
better.

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
