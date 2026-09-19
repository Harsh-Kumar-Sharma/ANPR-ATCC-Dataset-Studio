# 13: Free up space, at any moment

**What to build:** A place in the app that shows what is using disk and
lets you reclaim it, usable while everything else is running.

**Blocked by:** nothing

**Status:** ready-for-agent

Space is yours to manage - but you cannot manage what you cannot see,
and right now nothing in the app says where its gigabytes went. The
disk is at 9.4 GB free and the runs this plan enables are the ones that
will eat it.

It has to work *during* a training run. That is exactly when the disk
fills and exactly when stopping everything to tidy up is most
expensive.

- [ ] A Storage view lists what the app is holding, largest first: per project, per source, and by kind - source videos, track crops, materialised frames, dataset exports, job logs
- [ ] It shows free space on the drive and what the app's own total is
- [ ] Materialised frames can be cleared for a source or a project without touching labels: those images are a cache, recoverable by decoding the video again
- [ ] Track crops can be cleared the same way, once they are decoded on demand rather than stored
- [ ] A dataset export version can be deleted, with a warning if a training run used it
- [ ] Orphan workspace directories - belonging to no project - are found and can be removed; there are already two on this machine
- [ ] Every one of these is available while a job or a training run is going, and says what it reclaimed
- [ ] Nothing here can remove a label, an annotation or a frame row. Only pixels that can be regenerated, and exports the user names
