# 09: Import a dataset from disk

**What to build:** A YOLO dataset that already exists on disk can be
brought into the app as a dataset version, so it can be trained on and
merged with what this app produces.

**Blocked by:** nothing

**Status:** ready-for-agent

- [ ] Point the app at a folder holding `images/` and `labels/` and a `data.yaml`, and it becomes a dataset version
- [ ] Its classes are read from `data.yaml` and mapped onto the project's classes, with any that do not match reported rather than guessed
- [ ] The import is validated the way an export is: every image has a label file, every index is in range, every coordinate is normalised
- [ ] A malformed dataset is refused with the specific file and line, not a generic failure
- [ ] Imported versions are marked as imported, so it is always clear what this app produced and what came from elsewhere
- [ ] Images are referenced where they are by default rather than copied, since the disk has 9.4 GB free - copying is an explicit choice
