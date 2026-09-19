# 11: Feed the queue good frames, not near-duplicates

**What to build:** The labeling queue fills with frames worth a human's time — sharp, varied, and not fifteen near-identical shots of the same vehicle crossing the same lane.

Uncertainty sampling is deliberately out of scope: it needs a custom model to be uncertain about, which does not exist until much later. This ticket is the simple-heuristic version that works today.

**Blocked by:** 10 (Work through a labeling queue)

**Status:** done

- [x] A frame selection service decides which frames enter the queue, reusing the existing frame quality scoring rather than reimplementing it
- [x] Diversity heuristics spread the queue out — brightness, vehicle count, and perceptual-hash near-duplicate removal
- [x] Every queued frame records why it was selected, so a bad queue can be debugged rather than guessed at
- [x] Running selection over an existing processing run produces a visibly less repetitive queue than unfiltered frames

**Measured on real footage**, reproducibly - `scripts/selection_report.py` is in
the repo precisely so this claim is checkable. Over all 3,842 frames of
`atcc1.mp4`: 338 offered, 3,504 set aside, in about nine minutes.

The threshold was chosen from that measurement, not from taste. On a fixed
gantry camera the static background dominates a whole-frame hash: consecutive
frames differ by a median of 0 bits (p90 2), but *any two* frames of the clip
differ by a median of only 6 (p10 3). A threshold of 2 sits in that gap. The
first version used 6 and offered 43 frames of 3,842 - it was collapsing
genuinely different vehicles, which the synthetic tests could not show.

**Brightness and vehicle count mostly decide what counts as a duplicate**
rather than filtering: two frames that hash alike are the same scene only if
they hold the same number of vehicles under similar light. The one exception is
a frame with nothing detected at all, which is dropped outright. On this
footage brightness never discriminates (the clip spans 0.39-0.45); it earns its
keep on a source that runs into dusk.

**Quality reuses the existing scoring.** Detection stored the signals - blur,
area, confidence, truncation - on each frame-candidate row, and selection feeds
them back through `compute_composite_score`. A frame is worth as much as its
best detection.

**The quality floor rarely fires**, and that is worth knowing rather than
hiding: the detector's own confidence gate has already discarded the worst
observations, so the measured range is 0.26-0.72 against a floor of 0.2. It is
a guard against genuinely broken frames, not the main filter - near-duplicate
removal does the work.

**`skipped` is a distinct status from `rejected`.** "I looked and said no" and
"the machine never offered it" are different judgements; lumping them together
would make the progress line misdescribe what a human has actually reviewed.
Both are reversible through the same control.

**Selection does not cache what it decodes** - see the test. Caching wrote about
a gigabyte of JPEGs per source for pixels it reads once, which on a nearly-full
disk is the difference between a slow job and a failed one.
