# 03: Delete a frame for good

**What to build:** A frame can be deleted permanently from the canvas -
row gone, image file gone - as distinct from skipping it.

Skipping keeps a frame around so the decision can be undone. Some
frames do not deserve that: blank ones, blurred ones, frames where the
detector fired on nothing. Those are storage being spent on rubbish,
and on a disk with 9.4 GB free that matters.

**Blocked by:** nothing

**Status:** done

- [x] `DELETE /frames/{id}` removes the frame, its annotations, its detections and its materialised image file
- [x] It refuses if the frame is in an exported dataset version, because a version is immutable - the message says which version
- [x] The canvas offers Delete next to Skip, with the difference stated: skip is reversible, delete is not
- [x] Deleting moves to the next frame in the queue rather than leaving a blank canvas
- [x] The queue counts and the label balance both drop by one

**A track left with nothing in it goes too.** A track is its
observations; once the last one is deleted the row is an empty entry
in the track browser and a row the evaluation report still counts. Only
when it is genuinely empty - one frame of a track is not the track.

**The image is removed after the commit**, the same order the project
and source deletions settled on. A file left behind can be deleted by
hand; a row pointing at an image that is gone cannot be reasoned about.
The response says whether an image was there at all, since a frame
nobody has opened has never been decoded and costs nothing to delete.

**Delete always asks, even on an empty frame.** Skip asks only when
there are unsaved boxes, because skipping can be undone by putting the
frame back. This cannot be undone, and the two buttons sit next to
each other.
