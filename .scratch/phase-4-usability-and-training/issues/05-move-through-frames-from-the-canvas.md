# 05: Move through frames without leaving the image

**What to build:** Next and Previous sit under the frame you are
labelling, not only in the sidebar list.

**Blocked by:** 04 (Frames belong to a source)

**Status:** done

Navigation lives in the queue list today, so labelling means moving the
eye and the mouse away from the image after every frame. The keyboard
shortcuts exist; the buttons should be where the work is.

- [x] Previous and Next sit directly below the canvas, with the frame's position in the queue between them ("14 of 338")
- [x] They respect the source filter from ticket 04 - next means the next frame of this source
- [x] They are disabled at the ends rather than wrapping silently
- [x] Unsaved boxes still prompt before navigating, as they do now
- [x] The existing keyboard shortcuts keep working and are shown next to the buttons
- [x] The sidebar queue and the canvas stay in step, whichever one moved

**Notes:**

- The queue moved out of the panel into `useFrameQueue`. Two places
  now walk the same list, and two copies would be two lists disagreeing
  about which frame is next - which is exactly the thing the last
  criterion forbids.
- The unsaved-boxes question follows the click: asked from the sidebar
  it appears in the sidebar, asked from under the canvas it appears
  there. One prompt component, shown where the user is looking.
- Ctrl/Cmd with an arrow moves between frames. Bare arrows were taken -
  they nudge the selected box by a pixel - so the canvas now ignores
  arrows held with Ctrl/Cmd, otherwise it would shift the box on the
  way out of the frame.
- With no frame open, a step lands on the first or last rather than
  doing nothing: the user asked to move somewhere.
