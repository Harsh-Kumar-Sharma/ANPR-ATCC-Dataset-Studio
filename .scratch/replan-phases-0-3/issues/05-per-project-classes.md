# 05: Classes live in the project, seeded from a preset

**What to build:** Each project owns its own class list. Creating a project copies a preset into it, so editing one project's classes can never change another's. The existing review UI reads the class list from the project rather than from a hardcoded module.

Classes are currently twenty ATCC entries hardcoded in a core module and shared by every project. An ANPR project needs a different, much shorter list.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] A class definition table is owned by a project
- [ ] Presets exist for ATCC (the current twenty), ANPR (vehicle plus plate), and Blank
- [ ] Creating a project copies the chosen preset in — presets are never shared live
- [ ] A migration seeds every existing project with the ATCC v1 preset so nothing currently in the database breaks
- [ ] The existing review UI and its class validation read the project's classes, not the hardcoded module
