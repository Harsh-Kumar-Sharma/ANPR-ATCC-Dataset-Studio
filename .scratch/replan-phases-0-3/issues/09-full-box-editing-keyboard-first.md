# 09: Label a frame fully, keyboard-first

**What to build:** A user can label a frame with four vehicles in it end to end — drawing each box, adjusting the ones that are off, deleting mistakes, and assigning each box its class — driving the whole thing from the keyboard.

This is the ticket that makes labeling actually fast. Selection is track-level but labeling is frame-level: if a frame was chosen because it is the best shot of one vehicle, every other vehicle in it must be labeled too, because unlabeled objects teach the model that there is nothing there.

**Blocked by:** 08 (Draw one box on a frame and have it persist), 05 (Classes live in the project, seeded from a preset)

**Status:** ready-for-agent

- [ ] Boxes can be moved and resized by their edges and corners, and deleted
- [ ] Each box carries its own class, chosen from the project's class list
- [ ] Every common action has a keyboard path — class assignment, box cycling, delete, save — and the existing shortcut handling in the track review UI is the prior art to match
- [ ] A frame with several vehicles can be labeled completely without reaching for the mouse beyond drawing
- [ ] Boxes are clamped to the frame, and a zero-area drag does not create a box
