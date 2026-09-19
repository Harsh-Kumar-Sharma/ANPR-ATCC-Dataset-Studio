# 10: Merge datasets into one training set

**What to build:** Several dataset versions - from different sources,
or imported from elsewhere - combine into one training set.

**Blocked by:** 09 (Import a dataset from disk)

**Status:** ready-for-agent

- [ ] Pick several dataset versions and produce a merged one
- [ ] Class lists are reconciled by name, and a class present in one and not another is reported before the merge runs
- [ ] The train/val/test split is recomputed over the whole merged set rather than concatenating each version's splits, so one source cannot end up entirely in validation
- [ ] The same image appearing in two versions is included once, and the merge says how many duplicates it found
- [ ] The merged version records which versions it came from, so a training run can be traced back to its data
- [ ] A merged version exports and validates exactly like any other
