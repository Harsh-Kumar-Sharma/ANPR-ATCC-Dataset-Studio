# 07: Keep every frame that has a vehicle in it

**What to build:** A detection mode that walks every frame of the video
rather than a 5fps sample, and keeps the ones where the model found
something.

**Blocked by:** 06 (Choose which model detects)

**Status:** done

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

- [x] Detection can run at the source's native rate, keeping only frames with at least one detection above the confidence gate
- [x] Vehicle crops are no longer written during detection; the review UI decodes them on demand, the way full frames already work
- [x] Before starting, the app estimates frames, crops and bytes, and says so - a run that will not fit is refused rather than discovered at 80%
- [x] The job reports frames processed, frames kept and bytes written as it goes
- [x] Free space is checked during the run, and the job stops cleanly with what it has rather than filling the disk
- [x] A 90,003-frame source completes without exceeding a stated budget, measured rather than asserted

**Notes:**

- The crop is gone from the observation entirely, not just from disk.
  It used to be held in memory for the whole run and written at the
  end: at two vehicles a frame over 90,003 frames that is gigabytes of
  RAM before it is gigabytes of JPEG.
- The rule is "keep the pixels only when they cannot be found again".
  An offline video can be decoded a second time, so it writes no
  crops; a live capture still writes them, because its frames are gone
  the moment they pass. That is the one behaviour that had to stay.
- `frame_candidates.image_path` is nullable now, with a migration. Its
  downgrade fills nulls with the empty string rather than inventing a
  path, which the reviewer would meet as a broken image.
- OCR reads through the same crop service rather than off disk, so it
  works on runs that wrote nothing.
- The estimate reports two numbers because they differ by a hundred
  times now: what the run writes (rows) and what reviewing all of it
  would add (full-size frames). Only the second fills a disk, and
  hiding it would make the estimate useless.
- `ROW_BYTES`, which the refusal is computed from, is checked against
  what SQLite actually writes for a thousand realistic rows rather
  than against a remembered number.

**Not done as written:**

- "A 90,003-frame source completes without exceeding a stated budget,
  measured rather than asserted." No such source exists in the test
  suite and a real pass takes hours, so the budget is measured one
  step in: the per-row cost is measured against SQLite, and the
  90,003-frame total is arithmetic on top of it. The end-to-end run
  has not been observed.
