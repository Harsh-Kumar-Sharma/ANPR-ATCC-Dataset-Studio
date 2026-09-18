# 13: Record per-object attributes

**What to build:** Alongside a box's class, a user can record what else is true about that object — plate text, vehicle colour, direction, whether it is occluded, whether it is a night shot.

The plate-text card in the existing track review UI is the prior art and survives into this panel. Human-corrected plate text belongs here, in the annotation, rather than in the OCR candidate table, which is demoted to a model-prediction source.

This ticket is last and blocks nothing, deliberately. The replan names the attributes panel as the thing to defer if Phase 3 slips — leaving it at the end keeps that decision cheap.

**Blocked by:** 09 (Label a frame fully, keyboard-first)

**Status:** ready-for-agent

- [ ] An attributes panel edits the selected box's attributes and writes them into the annotation's generic attributes field
- [ ] Plate text is typed as a free string, reusing the existing plate-text UI's behaviour
- [ ] Attributes survive save and reload
- [ ] Adding a new attribute later requires no schema migration
- [ ] Attributes are optional — a frame with none of them still exports cleanly
