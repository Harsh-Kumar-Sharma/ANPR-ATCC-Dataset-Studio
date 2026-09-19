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

**A plate before the track is reviewed is refused, not stored somewhere else.**
409 `no_label_yet`, and the card says to review the track first. The ticket
allowed either that or finding somewhere for it to go; there is nowhere honest,
because an annotation is a *label* and the reviewer has not made one yet. The
old behaviour looked like it worked and lost the work - no export has ever read
that table.

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
