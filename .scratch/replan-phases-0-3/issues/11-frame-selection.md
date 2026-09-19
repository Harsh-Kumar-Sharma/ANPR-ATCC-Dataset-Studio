# 11: Feed the queue good frames, not near-duplicates

**What to build:** The labeling queue fills with frames worth a human's time — sharp, varied, and not fifteen near-identical shots of the same vehicle crossing the same lane.

Uncertainty sampling is deliberately out of scope: it needs a custom model to be uncertain about, which does not exist until much later. This ticket is the simple-heuristic version that works today.

**Blocked by:** 10 (Work through a labeling queue)

**Status:** done

- [x] A frame selection service decides which frames enter the queue, reusing the existing frame quality scoring rather than reimplementing it
- [x] Diversity heuristics spread the queue out — brightness, vehicle count, and perceptual-hash near-duplicate removal
- [x] Every queued frame records why it was selected, so a bad queue can be debugged rather than guessed at
- [x] Running selection over an existing processing run produces a visibly less repetitive queue than unfiltered frames

**Measured on real footage:** over a 60-frame slice of `atcc1.mp4`, selection
offered 3 frames and set aside 57 - near-identical shots of the same lane
collapsing to one. A full-source run over all 3,842 frames takes minutes (it
decodes every sampled frame), which is why it runs as a background job.

**Brightness and vehicle count are not filters.** They decide what counts as a
duplicate: two frames that hash alike are the same scene only if they hold the
same number of vehicles under similar light. Filtering on them directly would
throw away the variety the ticket asks for.

**Quality is not recomputed.** Detection already scored every observation and
those scores are on the frame-candidate rows; a frame is worth as much as its
best detection. Recomputing from pixels would also have meant a second decode.

**`skipped` is a distinct status from `rejected`.** "I looked and said no" and
"the machine never offered it" are different judgements; lumping them together
would make the progress line misdescribe what a human has actually reviewed.
Both are reversible through the same control.

**Selection does not cache what it decodes** - see the test. Caching wrote about
a gigabyte of JPEGs per source for pixels it reads once, which on a nearly-full
disk is the difference between a slow job and a failed one.
