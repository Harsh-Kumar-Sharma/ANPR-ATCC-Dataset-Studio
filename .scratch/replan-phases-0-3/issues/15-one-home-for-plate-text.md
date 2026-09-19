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

**Status:** ready-for-agent

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

- [ ] Human-corrected plate text is stored in one place, and it is the annotation
- [ ] A correction made before a track is accepted still has somewhere to go, or the UI does not offer it until there is one
- [ ] The OCR agreement metric states what it measures once human rows are no longer in that table, and its tests say so
- [ ] Plate text reaches the exported dataset whichever UI recorded it
- [ ] The canvas shows what the model read, so a labeller is not retyping a plate the model already has
- [ ] `docs/REPLAN.md` lines about the plate card being replaced are true, or corrected
