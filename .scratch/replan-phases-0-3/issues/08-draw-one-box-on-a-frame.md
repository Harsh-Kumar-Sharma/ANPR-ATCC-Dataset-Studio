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

**From review (fixed in `HEAD`):** a save deleted and re-inserted every box, which
cascaded away an exported box's dataset item and un-reviewed its track on every
save. The canvas now echoes the ids of boxes it loaded, and those are updated in
place; only a box it no longer sends is removed. The migration's orphan delete
did not cascade either; it does now, and there is an alembic test on a
throwaway database for both directions. The queue excludes frames from live
RTSP sessions that have no stored image, since they cannot be opened.

**Named limitation, not fixed here:** `query_approved_items`, evaluation's class
distribution and the active-learning queue still join through the frame
candidate, so boxes drawn on the canvas are invisible to export until ticket 12
rewrites it onto frames. Draw fifty boxes today and export produces the legacy
track labels only. Evaluation and active-learning belong to no ticket yet.

**Incident:** the pre-fix smoke test on the real database deleted one legacy label
(frame 234 of '3gp Real Video Test') through the bug above. It was restored
exactly - original id, class, bbox to 0.0000 px, both dataset items, track back
to accepted - from the v2/v3 export manifests on disk, which is precisely the
record those manifests exist to be.

**Second round:** the incident took three dataset-item rows, not two - v1's
manifest records the annotation at item level, which the first recovery did not
walk. All three are back and every manifest item now has a row. The set a save
replaces is *every* box on the frame: a prediction echoed back becomes the
human's box, one left out is removed, and a reviewed box keeps its decision when
its geometry is edited. The queue's "can be opened" filter is by source type; a
video source whose file has since gone missing is still listed and errors on
click with a clear message rather than being hidden.
