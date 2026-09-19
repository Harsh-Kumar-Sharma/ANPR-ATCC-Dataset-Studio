# 13: Free up space, at any moment

**What to build:** A place in the app that shows what is using disk and
lets you reclaim it, usable while everything else is running.

**Blocked by:** nothing

**Status:** done

Space is yours to manage - but you cannot manage what you cannot see,
and right now nothing in the app says where its gigabytes went. The
disk is at 9.4 GB free and the runs this plan enables are the ones that
will eat it.

It has to work *during* a training run. That is exactly when the disk
fills and exactly when stopping everything to tidy up is most
expensive.

- [x] A Storage view lists what the app is holding, largest first: per project, per source, and by kind - source videos, track crops, materialised frames, dataset exports, job logs
- [x] It shows free space on the drive and what the app's own total is
- [x] Materialised frames can be cleared for a source or a project without touching labels: those images are a cache, recoverable by decoding the video again
- [ ] Track crops can be cleared the same way, once they are decoded on demand rather than stored — **waiting on ticket 07.** They are shown in the breakdown, but clearing them today would leave the track review screen with missing images, because nothing decodes them again. The button arrives when 07 makes them lazy.
- [x] A dataset export version can be deleted, with a warning if a training run used it
- [x] Orphan workspace directories - belonging to no project - are found and can be removed; there are already two on this machine
- [x] Every one of these is available while a job or a training run is going, and says what it reclaimed
- [x] Nothing here can remove a label, an annotation or a frame row. Only pixels that can be regenerated, and exports the user names

**Decoded frames are the safe one, and the panel says why.** Clearing
them deletes the files and sets `Frame.image_path` back to null, which
is already how the materialiser spells "not decoded yet" - so the next
time the canvas opens one it comes straight back out of the source
video. Labels, boxes and frame rows are untouched, which is what makes
this usable in the middle of a long run rather than only between runs.

**Deleting a dataset version releases the frames it was holding.** A
frame in an exported version cannot be deleted, because the manifest
names it. Once the version goes, it can - and the labels it was made
from stay, so the same frames export again next time.

**Job files are cleaned up for finished jobs only.** A running worker
is still writing to its own progress file. Files belonging to no job
row at all go too: those are the leftovers of jobs dismissed before
ticket 02 made dismissal take them along.
