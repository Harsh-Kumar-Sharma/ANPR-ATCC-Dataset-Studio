# 07: Delete or merge a class without orphaning labels

**What to build:** When a user deletes a class that labels are using, the app asks where those labels should go — moved to another class, or deleted with it — and applies that choice. Orphaned labels are the easiest way to silently poison a dataset, so the app never creates one.

**Blocked by:** 06 (Edit a project's class schema)

**Status:** ready-for-agent

- [ ] Deleting a class in use prompts with the actual count, e.g. "these 47 labels use this class — move them where, or delete them?"
- [ ] Choosing a target class remaps every affected label to it
- [ ] Choosing deletion removes the labels along with the class
- [ ] Merging two classes is expressible through the same path
- [ ] After any of these, no label references a class that no longer exists
