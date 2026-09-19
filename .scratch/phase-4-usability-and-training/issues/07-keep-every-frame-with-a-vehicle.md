# 07: Keep every frame that has a vehicle in it

**What to build:** A detection mode that walks every frame of the video
rather than a 5fps sample, and keeps the ones where the model found
something.

**Blocked by:** 06 (Choose which model detects)

**Status:** ready-for-agent

**This ticket is mostly about disk, not about sampling.** The sampling
change is one parameter. What makes it hard is that the disk has 9.4 GB
free and `day_anpr_gantry.mp4` is 90,003 frames.

Today every observation writes a vehicle crop to disk as it goes. At
two vehicles a frame and 40 KB a crop, a full pass over that video is
over 7 GB of crops alone - and if those frames are later materialised
for labelling at 1080p, another 18-36 GB. The run would fill the disk
before it finished. A selection trial in an earlier session already
wrote 1.2 GB of orphan JPEGs before anyone noticed.

So the mode only ships with the pixels made lazy.

- [ ] Detection can run at the source's native rate, keeping only frames with at least one detection above the confidence gate
- [ ] Vehicle crops are no longer written during detection; the review UI decodes them on demand, the way full frames already work
- [ ] Before starting, the app estimates frames, crops and bytes, and says so - a run that will not fit is refused rather than discovered at 80%
- [ ] The job reports frames processed, frames kept and bytes written as it goes
- [ ] Free space is checked during the run, and the job stops cleanly with what it has rather than filling the disk
- [ ] A 90,003-frame source completes without exceeding a stated budget, measured rather than asserted
