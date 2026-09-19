# 03: Delete a frame for good

**What to build:** A frame can be deleted permanently from the canvas -
row gone, image file gone - as distinct from skipping it.

Skipping keeps a frame around so the decision can be undone. Some
frames do not deserve that: blank ones, blurred ones, frames where the
detector fired on nothing. Those are storage being spent on rubbish,
and on a disk with 9.4 GB free that matters.

**Blocked by:** nothing

**Status:** ready-for-agent

- [ ] `DELETE /frames/{id}` removes the frame, its annotations, its detections and its materialised image file
- [ ] It refuses if the frame is in an exported dataset version, because a version is immutable - the message says which version
- [ ] The canvas offers Delete next to Skip, with the difference stated: skip is reversible, delete is not
- [ ] Deleting moves to the next frame in the queue rather than leaving a blank canvas
- [ ] The queue counts and the label balance both drop by one
