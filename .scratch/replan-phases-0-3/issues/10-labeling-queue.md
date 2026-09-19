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
