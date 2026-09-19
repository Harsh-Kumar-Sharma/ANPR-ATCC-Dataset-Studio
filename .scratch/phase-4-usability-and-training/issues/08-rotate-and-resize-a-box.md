# 08: Rotate and resize a box, like CVAT

**What to build:** A box on the canvas can be rotated, not only moved
and resized, so a skewed plate or a vehicle at an angle gets a tight
label instead of a loose rectangle around it.

**Blocked by:** 05 (Move through frames from the canvas)

**Status:** needs-a-decision

**There is a decision here and it changes the size of the work.**
Rotation is easy to draw and hard to export, because a rotated box is
not what the current dataset format holds. `bbox_json` is
`[x1, y1, x2, y2]` and the exporter writes YOLO's `class cx cy w h`.

*Option A - rotation all the way through.* Store an angle, export YOLO
OBB (four corner points per box), and train an OBB model. Most accurate
for plates on a gantry, which are heavily skewed. Costs: a second
export format, a validator that understands it, a separate model family
for training, and every downstream consumer updated.

*Option B - rotation for labelling only.* The labeller rotates for a
tight fit; the export writes the axis-aligned box that encloses it. No
format change, no training change, and the rotation is lost in the
dataset.

**My recommendation is A, but after group E.** Get training working on
the format that already exists, then change the format once with
training in place to verify it end to end. Doing both at once means
debugging a new label format and a new training path together.

- [ ] Decide A or B and record the decision in this ticket before building
- [ ] A selected box has a rotation handle, and rotating is undoable
- [ ] Resizing respects the rotation rather than snapping back to axis-aligned
- [ ] The angle round-trips through save and reload
- [ ] Keyboard nudge rotates as well as moves
- [ ] (If A) The export writes YOLO OBB, the validator checks it, and a rotated dataset trains
