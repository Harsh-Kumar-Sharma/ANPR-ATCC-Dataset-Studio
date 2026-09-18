# 08: Draw one box on a frame and have it persist

**What to build:** A user opens a frame, drags a box on it, saves, reloads, and the box is still there. The thinnest complete path through the new frame-level labeling subsystem — and the prefactor that makes every later labeling ticket an easy change.

Labels currently hang off a frame *candidate*, which is a per-detection crop, and the review service upserts a single annotation per track. Both facts make a second box on a frame impossible. This ticket moves labels onto the frame itself so that many boxes per frame becomes structurally possible; the editing affordances that exploit it come next.

Only about twenty annotations exist today and the replan explicitly judges them not worth a migration path, so the schema move is a single step rather than an expand–contract sequence.

**Blocked by:** None (can start immediately)

**Status:** done

- [x] Annotations hang off a frame rather than a frame candidate, and carry a generic attributes field so future attributes never need another migration
- [x] A frame carries a status (pending / labeled / rejected) and a selection reason recording why it entered the queue
- [x] A frames API can list a labeling queue, serve a frame's image, and save all annotations for a frame in one call
- [x] Saving replaces the frame's full set of boxes, so deleting a box actually deletes it
- [x] A labeling canvas drag-draws a box on a served frame and saves it
- [x] Reopening the frame shows the saved box in the right place
- [x] The existing review and dataset-query paths still run after the schema move — anything broken by it is fixed or explicitly removed here, not left failing

**Landed as two commits:** the schema move first (`5c4f808`), green on its own,
then the queue, the frames API and the canvas. A canvas save replaces every
human box on the frame - including one written through track review, which
the canvas shows - and a track whose label is thereby removed goes back to
`unreviewed`, through the same seam deleting a class uses.

**Verified on the real database:** a 1920x1080 frame decoded on demand, one
box saved with class and attributes, reopened at identical coordinates.
