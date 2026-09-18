# 06: Edit a project's class schema

**What to build:** A user can add a class to their project and rename an existing one, and see the change reflected wherever classes are shown. Renaming is always safe — it touches no labels.

**Blocked by:** 05 (Classes live in the project, seeded from a preset)

**Status:** done

- [x] A class schema editor lists the project's classes and allows adding and renaming
- [x] A renamed class shows up in the review UI without any label changing
- [x] Class names are unique within a project, and the editor says so rather than failing silently
- [x] Deleting is either absent here or refuses when the class is in use — the safe remap path is ticket 07

**On the last criterion:** deleting is present but refuses while a class is
in use, and the editor asks for the count *before* offering the delete, so the
refusal is explained rather than merely returned. `count_labels_using` is the
number ticket 07's "these 47 labels use this class" prompt will be built from.
