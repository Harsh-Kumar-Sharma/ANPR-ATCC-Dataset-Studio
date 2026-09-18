# 07: Delete or merge a class without orphaning labels

**What to build:** When a user deletes a class that labels are using, the app asks where those labels should go — moved to another class, or deleted with it — and applies that choice. Orphaned labels are the easiest way to silently poison a dataset, so the app never creates one.

**Blocked by:** 06 (Edit a project's class schema)

**Status:** done

- [x] Deleting a class in use prompts with the actual count, e.g. "these 47 labels use this class — move them where, or delete them?"
- [x] Choosing a target class remaps every affected label to it
- [x] Choosing deletion removes the labels along with the class
- [x] Merging two classes is expressible through the same path
- [x] After any of these, no label references a class that no longer exists

**Two consequences of deleting labels that the ticket did not name, handled:**
a track whose only review was deleted goes back to `unreviewed` rather than
claiming a review that no longer exists; and the annotation's `DatasetItem`
rows go with it, because SQLite is not enforcing foreign keys here and they
would otherwise sit silently pointing at nothing. The export on disk remains
the durable record of what a version contained.

**Rejected reviews** still carry a `class_id` and are counted and remapped or
deleted like any other label; the prompt's count includes them, and the
refusal message says so.
