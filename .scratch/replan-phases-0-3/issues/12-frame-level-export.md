# 12: Export labeled frames as a valid multi-box YOLO dataset

**What to build:** Work done in the labeling canvas travels all the way out into a training-ready YOLO dataset. This is the seam that makes the labeling subsystem *connected* rather than merely complete, and it is Phase 3's done-when.

The export path currently queries through annotation to frame candidate to track to run to source, which is track-keyed and cannot express several objects on one image. Frame grouping already exists inside the exporter; the queries underneath it are what change.

**Blocked by:** 08 (Draw one box on a frame and have it persist), 09 (Label a frame fully, keyboard-first)

**Status:** ready-for-agent

- [ ] The dataset query is frame-level rather than track-keyed
- [ ] Export emits one label file per image containing every box on that frame
- [ ] Rejected frames and unlabeled frames are excluded
- [ ] Fifty frames labeled in the canvas export as a valid YOLO dataset and the existing dataset validator passes it
- [ ] The existing split logic still applies and is not bypassed
