# 06: Edit a project's class schema

**What to build:** A user can add a class to their project and rename an existing one, and see the change reflected wherever classes are shown. Renaming is always safe — it touches no labels.

**Blocked by:** 05 (Classes live in the project, seeded from a preset)

**Status:** ready-for-agent

- [ ] A class schema editor lists the project's classes and allows adding and renaming
- [ ] A renamed class shows up in the review UI without any label changing
- [ ] Class names are unique within a project, and the editor says so rather than failing silently
- [ ] Deleting is either absent here or refuses when the class is in use — the safe remap path is ticket 07
