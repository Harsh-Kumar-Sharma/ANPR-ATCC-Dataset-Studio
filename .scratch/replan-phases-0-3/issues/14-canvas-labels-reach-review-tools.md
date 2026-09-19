# 14: Make canvas labels visible to the tools that review labelling

**What to build:** A user who labels entirely in the canvas can see the class
balance of their own work and can be told when their class disagrees with the
detector's. Today both screens answer "nothing", and "nothing" is
indistinguishable from "nothing wrong".

Found during the review of ticket 12, not by a user. Ticket 12 made canvas boxes
reach the dataset; it did not make them reach the two screens that exist to tell
a labeller whether their labelling is any good.

**Blocked by:** 12 (Export labeled frames as a valid multi-box YOLO dataset)

**Status:** ready-for-agent

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

- [ ] A canvas-drawn box whose class contradicts the detector's class on an overlapping detection appears in the disagreement queue
- [ ] The overlap rule is chosen explicitly and stated, not left implicit in a magic number
- [ ] A canvas box matching no detection is handled deliberately, and the choice is recorded
- [ ] A labeller can see the class balance of their own labelled frames, whatever path the labels were written by
- [ ] Where a screen genuinely cannot answer for canvas labels, it says so rather than rendering "None"
