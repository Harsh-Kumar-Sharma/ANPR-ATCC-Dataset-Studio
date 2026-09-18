# 10: Work through a labeling queue

**What to build:** A user sits down and labels a run of frames one after another, seeing how far through they are, and skipping frames that are not worth labeling.

**Blocked by:** 08 (Draw one box on a frame and have it persist)

**Status:** ready-for-agent

- [ ] Next and previous move through the queue without losing unsaved work silently
- [ ] Progress against the queue is visible — labeled, rejected, remaining
- [ ] A frame can be rejected, and rejected frames leave the queue and stay out of any dataset
- [ ] Frame status reflects what has actually been done to each frame
- [ ] Reopening the app returns the user to where they left off rather than to the start
