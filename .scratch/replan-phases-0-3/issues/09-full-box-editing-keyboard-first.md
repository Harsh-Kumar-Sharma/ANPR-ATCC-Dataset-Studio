# 09: Label a frame fully, keyboard-first

**What to build:** A user can label a frame with four vehicles in it end to end — drawing each box, adjusting the ones that are off, deleting mistakes, and assigning each box its class — driving the whole thing from the keyboard.

This is the ticket that makes labeling actually fast. Selection is track-level but labeling is frame-level: if a frame was chosen because it is the best shot of one vehicle, every other vehicle in it must be labeled too, because unlabeled objects teach the model that there is nothing there.

**Blocked by:** 08 (Draw one box on a frame and have it persist), 05 (Classes live in the project, seeded from a preset)

**Status:** done

- [x] Boxes can be moved and resized by their edges and corners, and deleted
- [x] Each box carries its own class, chosen from the project's class list
- [x] Every common action has a keyboard path — class assignment, box cycling, delete, save — and the existing shortcut handling in the track review UI is the prior art to match
- [x] A frame with several vehicles can be labeled completely without reaching for the mouse beyond drawing
- [x] Boxes are clamped to the frame, and a zero-area drag does not create a box

**Keyboard map:** `[`/`]` cycle boxes, `1`-`9` assign the class at that position
in the project's list, `0` clears it, `Del` removes the box, arrows nudge by one
frame pixel (`Shift` by ten), `S` saves, `Esc` deselects. The number-key mapping
is what makes a four-vehicle frame labellable without the mouse after drawing;
there is a test that does exactly that.

**Note:** `isEditableTarget` moved out of `TrackReview` into `src/keyboard.ts` -
both panels put single-letter shortcuts on the window, so both need the same
rule that typing into a field never triggers them.

**From review (fixed):** a resize could collapse a box onto the opposite edge,
producing zero area the server rejects on save - invisible, and impossible for
the user to locate. A dragged edge now stops `MIN_BOX_SIDE` short of the edge it
pivots on; crossing over still flips. Escape mid-drag left the box where it had
been dragged with Save disabled, stranding the edit; it puts the box back now.
An edit made while a save was in flight was overwritten by the response; the
response is dropped instead, since the user's boxes are newer. Selection follows
the box by id rather than by position, and the saved set is restored in the
order it was sent, because the server orders by `updated_at` and an edited box
can come back somewhere else.

**Not done, and not required here:** there is no keyboard *resize* - arrows move
a box, they do not reshape it. The ticket asks for adjustment from the keyboard
and the documented keymap says move; worth a follow-up if labelling turns out to
need it.
