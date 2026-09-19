# 13: Record per-object attributes

**What to build:** Alongside a box's class, a user can record what else is true about that object — plate text, vehicle colour, direction, whether it is occluded, whether it is a night shot.

The plate-text card in the existing track review UI is the prior art and survives into this panel. Human-corrected plate text belongs here, in the annotation, rather than in the OCR candidate table, which is demoted to a model-prediction source.

This ticket is last and blocks nothing, deliberately. The replan names the attributes panel as the thing to defer if Phase 3 slips — leaving it at the end keeps that decision cheap.

**Blocked by:** 09 (Label a frame fully, keyboard-first)

**Status:** done

- [x] An attributes panel edits the selected box's attributes and writes them into the annotation's generic attributes field
- [x] Plate text is typed as a free string, reusing the existing plate-text UI's behaviour
- [x] Attributes survive save and reload
- [x] Adding a new attribute later requires no schema migration
- [x] Attributes are optional — a frame with none of them still exports cleanly

**One list, served, rather than a copy on each side.** `ATTRIBUTE_DEFINITIONS`
in `annotation_attributes.py` is what the canvas renders its controls from *and*
what a save is checked against. Hard-coding the list in the UI as well is how a
dropdown ends up offering a value the server refuses, and the first sign of it
is a user who cannot save.

**Adding an attribute is a line in that list.** No migration, because the values
live in the annotation's generic JSON column. That is the criterion, and it is
also the only reason the list is a Python constant rather than a table: a table
would buy editability nobody asked for and cost a migration every time its shape
changed. These are properties of the domain - a vehicle has a plate, a colour, a
direction - unlike classes, which are per project because two projects really do
count different things.

**Unknown keys are refused.** The field is schema-free so that *adding* an
attribute is cheap, not so that anything at all can be written into it. A
silently accepted `plate_txt` is a value nothing will ever read again. Checked
against the real database first: all twenty existing annotations have empty
attributes, so nothing already stored can fail to re-save.

**Clearing a field removes the key.** An empty string and a missing key mean the
same thing to the person who typed them, and letting both exist means every
later reader has to test for both. `clean_attributes` normalises rather than
only validating, so what is on screen and what is in the database are the same
shape.

**Booleans are checked as booleans, not for truthiness.** JSON `"true"` and `1`
are both truthy and neither is a boolean; storing one makes every later read of
that field quietly wrong.

**Attributes go into the export manifest.** A YOLO label line has room for a
class and four numbers and nothing else, so without this the plate text someone
typed would never leave the database and the panel would be a diary rather than
a labelling tool.

**Night shot is per box, and that is a compromise.** Lighting is a property of
the frame, not of one vehicle in it, so two boxes on one frame can disagree
about it. It sits on the box anyway because the box is the only thing with a
generic attributes field, and giving frames one is the migration this ticket
exists to avoid. Worth moving the day frames grow attributes of their own.

**The OCR candidate table is NOT demoted, deliberately.** The ticket's prose
says human-corrected plate text belongs in the annotation rather than there.
Half of that is now true - the canvas writes plate text onto the box. Rewiring
track review's correction card to do the same is not a tidy-up: corrections can
be made before a track is accepted, when no annotation exists to hold them, and
`compute_ocr_agreement` counts human rows when it scores OCR, so moving them
changes an evaluation metric. That is its own ticket, not a footnote to this
one. None of the five acceptance criteria depends on it.

**A pre-existing test used a made-up attribute key** (`{"moved": True}`) as a
marker for "these attributes were written". It now uses a real one. Worth noting
because it is the only behaviour change this ticket makes to code outside the
panel.
