# 10: Work through a labeling queue

**What to build:** A user sits down and labels a run of frames one after another, seeing how far through they are, and skipping frames that are not worth labeling.

**Blocked by:** 08 (Draw one box on a frame and have it persist)

**Status:** done

- [x] Next and previous move through the queue without losing unsaved work silently
- [x] Progress against the queue is visible — labeled, rejected, remaining
- [x] A frame can be rejected, and rejected frames leave the queue and stay out of any dataset
- [x] Frame status reflects what has actually been done to each frame
- [x] Reopening the app returns the user to where they left off rather than to the start

**On "stay out of any dataset":** `query_approved_items` now excludes labels on
a rejected frame. The label itself survives - rejecting means "not worth
labelling", not "delete my work" - so putting the frame back restores it to the
export. Without this the judgement would have been decorative.

**Unsaved work** is guarded, not blocked: navigating away asks, and the user can
discard and go or stay and keep working. Refusing to move at all would be worse
than the question.

**Resume** is per-project in `localStorage`, restored once on arrival and only
when nothing is already open. It is this machine's view of a shared project, and
losing it costs a scroll rather than work.

**From review (fixed):** Skip discarded unsaved boxes without asking - the same
criterion the queue's own navigation guard exists to satisfy. It asks now.
Putting a skipped frame back left it reporting `pending` even when it was full of
boxes, which also made the progress counts wrong; status is derived from what is
actually on the frame. The reversibility these notes claimed had no UI at all -
there is a "show skipped" toggle and a per-frame "put back" now. Skipping also
no longer ejects the user out of labelling; the queue moves on.

**Also:** the queue drops stale responses from overlapping refreshes, the canvas
clears its unsaved-work flag on the way out (it was leaving the queue offering to
discard boxes that unmounting had already destroyed), queue eligibility is
written once so the list and the counts cannot disagree, and a label on a
rejected frame no longer blocks deleting its class - that frame is out of the
dataset, so its labels should not hold a class hostage.
