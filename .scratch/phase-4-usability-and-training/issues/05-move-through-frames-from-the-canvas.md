# 05: Move through frames without leaving the image

**What to build:** Next and Previous sit under the frame you are
labelling, not only in the sidebar list.

**Blocked by:** 04 (Frames belong to a source)

**Status:** ready-for-agent

Navigation lives in the queue list today, so labelling means moving the
eye and the mouse away from the image after every frame. The keyboard
shortcuts exist; the buttons should be where the work is.

- [ ] Previous and Next sit directly below the canvas, with the frame's position in the queue between them ("14 of 338")
- [ ] They respect the source filter from ticket 04 - next means the next frame of this source
- [ ] They are disabled at the ends rather than wrapping silently
- [ ] Unsaved boxes still prompt before navigating, as they do now
- [ ] The existing keyboard shortcuts keep working and are shown next to the buttons
- [ ] The sidebar queue and the canvas stay in step, whichever one moved
