# 08: Draw one box on a frame and have it persist

**What to build:** A user opens a frame, drags a box on it, saves, reloads, and the box is still there. The thinnest complete path through the new frame-level labeling subsystem — and the prefactor that makes every later labeling ticket an easy change.

Labels currently hang off a frame *candidate*, which is a per-detection crop, and the review service upserts a single annotation per track. Both facts make a second box on a frame impossible. This ticket moves labels onto the frame itself so that many boxes per frame becomes structurally possible; the editing affordances that exploit it come next.

Only about twenty annotations exist today and the replan explicitly judges them not worth a migration path, so the schema move is a single step rather than an expand–contract sequence.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] Annotations hang off a frame rather than a frame candidate, and carry a generic attributes field so future attributes never need another migration
- [ ] A frame carries a status (pending / labeled / rejected) and a selection reason recording why it entered the queue
- [ ] A frames API can list a labeling queue, serve a frame's image, and save all annotations for a frame in one call
- [ ] Saving replaces the frame's full set of boxes, so deleting a box actually deletes it
- [ ] A labeling canvas drag-draws a box on a served frame and saves it
- [ ] Reopening the frame shows the saved box in the right place
- [ ] The existing review and dataset-query paths still run after the schema move — anything broken by it is fixed or explicitly removed here, not left failing
