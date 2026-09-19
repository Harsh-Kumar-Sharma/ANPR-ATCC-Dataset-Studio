# 15: One home for human-corrected plate text

**What to build:** A human's plate reading lives in one place. Today it lives in
two, and only one of them is a per-object label.

`docs/REPLAN.md` said the track-review plate card would be *replaced* by the
attributes panel and that human-corrected plate text would live in
`Annotation.attributes`. Ticket 13 built half of that: the canvas can record a
plate on a box. The OCR card is untouched, so the app now has two live
plate-text UIs writing to two tables. The replan lines that describe this as
done are, for now, wrong.

**Blocked by:** 13 (Record per-object attributes)

**Status:** done

The two are not merely duplicated, they behave differently.

**The canvas plate exports; the OCR one does not.** `dataset_export.py` has no
reference to OCR at all, so a plate corrected in track review never leaves the
database. That is exactly the "diary rather than a labelling tool" complaint
ticket 13 made about not exporting attributes, and it applies verbatim to the
card that was supposed to be replaced.

**They are at least comparable now.** Ticket 13's review caught that the canvas
stored raw typing while the OCR path stores `normalize_plate_text` output, so
the same vehicle's plate could never compare equal across the two. The canvas
now canonicalises the same way. That makes a migration possible rather than
making it unnecessary.

**Two things make this more than a move**, and are why ticket 13 deliberately
did not attempt it:

* A correction can be made before a track is accepted, when no annotation
  exists to hold it. `PUT /tracks/{id}/ocr-selection` needs only a track and a
  frame candidate.
* `compute_ocr_agreement` counts human rows when it scores OCR. Demoting the
  table to model predictions changes an evaluation metric, which needs its own
  decision about what the metric should then mean.

- [x] Human-corrected plate text is stored in one place, and it is the annotation
- [x] A correction made before a track is accepted still has somewhere to go, or the UI does not offer it until there is one
- [x] The OCR agreement metric states what it measures once human rows are no longer in that table, and its tests say so
- [x] Plate text reaches the exported dataset whichever UI recorded it
- [x] The canvas shows what the model read, so a labeller is not retyping a plate the model already has
- [x] `docs/REPLAN.md` lines about the plate card being replaced are true, or corrected

**`PUT /tracks/{id}/ocr-selection` is gone, replaced by `PUT
/tracks/{id}/plate-text`.** The old endpoint did two things - pick one of the
model's readings, or supply your own - and recorded both as a row in the model's
table. Clicking a candidate and typing the same characters are the same act, so
there is one field now and it writes the annotation. The client already has the
candidate's text; the server has no reason to know which way it arrived, and
keeping a "which one did they pick" flag was how the reading ended up in two
places.

**A plate before the track is reviewed is not offered, and that took two goes.**
The first version accepted the typing and refused it on save with a 409 saying
"review it first". Review pointed out that the remedy throws the plate away: the
accept control navigates to the next track and the component is keyed by track
id, so the reading the user had just taken off the image is gone. The field is
disabled now, with the reason on screen, and becomes usable the moment the track
is reviewed. The server still refuses - it has to - but nobody should reach it.
There is nowhere honest for a plate before there is a label, because an
annotation is the label.

**`selected` now means one thing: the model's own best attempt.** The OCR run
sets it and nothing else writes it. It used to mean "the track's chosen result",
which a human could change, which is what made the agreement metric compare the
model against itself.

**The agreement metric changed meaning, and the old meaning was misleading.**
It compared the model's top attempt with whichever row was `selected` - but the
OCR run selects the model's own best, so a track nobody had looked at scored an
agreement. The rate was largely a measure of how little reviewing had been done.
It now compares what the model read with what a person wrote down, and counts
only tracks where both exist. Expect the number to drop and the denominator to
shrink; that is the point.

**Plate readings are offered by frame, not by track.** The canvas holds a
picture and has no track, so `GET /frames/{id}/plate-readings` returns what the
model read on it, carrying the *detection's* box rather than the plate's - the
plate box is relative to a cropped vehicle image, which is meaningless on a
frame. Every reading on the frame is offered rather than only the ones
overlapping the selected box: matching them would mean a second copy of the
association threshold `active_learning` owns, and three lines is a list a person
can read.

**The migration moves what exists and refuses to lose what it cannot place.** A
human row whose track has no annotation stays where it is, inert, and the
upgrade prints how many. Losing someone's typing to a migration would be worse
than leaving a row behind. A plate already on the annotation wins: one typed on
the canvas is the newer of the two by construction, and a migration cannot see
which the user meant. The real database has no human rows at all, so this is
insurance rather than a move.

## Review found five ways this could lose data, and it was right about all of them

**The migration deleted readings it had not moved.** Three separate paths.
Several human rows on one track - which the old flow produced every time someone
corrected a plate twice - moved the first and deleted the rest, and because the
survivor was chosen by row order rather than by `selected`, the one the old UI
showed as current was usually the one thrown away. A reading the annotation's
own plate disagreed with was deleted too, so "the annotation wins" quietly meant
"the other value is destroyed". And the count it printed was of *deleted* rows,
so the one number an operator saw was wrong in exactly the cases that mattered.

It now picks the reading the old UI showed as current, deletes a row only once
its text is safely on the annotation or the annotation already says the same
thing, and leaves everything else alone: no label to attach to, a disagreeing
plate, or a reading too long to store. Those are counted apart and reported.

**It could also write a plate the app then refuses.** The old endpoint had no
length limit and the attribute validator has one, so a long reading written
straight onto the annotation made every save of that frame fail - the same
lockout the previous commit existed to remove, through a path that skips the
validator. Such a reading is now left where it is, and `clean_attributes` treats
an over-long *echo* the way it already treated retired keys, so a value already
in the database can never lock the frame carrying it.

**`plate-readings` read the rows the migration promised nothing reads.** Human
rows were written with confidence 1.0 and the query sorts by confidence, so a
leftover would have sorted first under a heading that says "Model read:". It
filters on `source == "model"` now, and deduplicates: frames outlive runs, so a
second OCR pass piles more rows onto the same frame and the same plate read four
times is one suggestion.

**`selected` could be true on two rows.** Removing human selection removed the
only code that cleared it, so a second OCR run left the previous run's best
flagged as well - which the model's own docstring forbids. The run clears the
track's rows before flagging its best. That docstring was also still describing
the world before this ticket, in three separate claims, and now describes this
one.

**Track review reported saves that had not happened.** It echoed the typed text
rather than what came back, so "mh 12 ab 1234" displayed lowercase while
`MH12AB1234` was stored, and typing `!!!` over a plate wiped it while announcing
"Plate saved: !!!". It shows what the server stored.

**Smaller things.** The inlined normaliser used `str.isalnum`, which is
Unicode-aware where the app's is ASCII-only, so it could write values the app
could never produce - a test now compares the two on real inputs, and the same
test pins the inlined length limit. `plate_text` was defaulted to empty, so a
request that forgot the field silently cleared a reading; it is required. The
`try/except` in `set_track_plate_text` re-raised the same error unchanged. The
duplicate `_human_annotation` now uses `review.get_human_annotation`. The
reading payload carried a detection bbox that three docstrings defended and
nothing read, so it is gone. And `TrackReview.tsx` had no tests at all - the
screen this ticket is actually about - which is how the stale display shipped.

**One claim of mine overstated the metric.** Its docstring said the plates come
from the annotations, "which is where a human's reading lives". They come from
the annotations a *track review* produced: the join runs through the frame
candidate, so a plate typed on a canvas-drawn box is not counted. That is
inherent to a per-track metric, and the docstring says so now instead of
implying coverage it does not have.
