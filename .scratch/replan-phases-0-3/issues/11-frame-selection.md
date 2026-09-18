# 11: Feed the queue good frames, not near-duplicates

**What to build:** The labeling queue fills with frames worth a human's time — sharp, varied, and not fifteen near-identical shots of the same vehicle crossing the same lane.

Uncertainty sampling is deliberately out of scope: it needs a custom model to be uncertain about, which does not exist until much later. This ticket is the simple-heuristic version that works today.

**Blocked by:** 10 (Work through a labeling queue)

**Status:** ready-for-agent

- [ ] A frame selection service decides which frames enter the queue, reusing the existing frame quality scoring rather than reimplementing it
- [ ] Diversity heuristics spread the queue out — brightness, vehicle count, and perceptual-hash near-duplicate removal
- [ ] Every queued frame records why it was selected, so a bad queue can be debugged rather than guessed at
- [ ] Running selection over an existing processing run produces a visibly less repetitive queue than unfiltered frames
