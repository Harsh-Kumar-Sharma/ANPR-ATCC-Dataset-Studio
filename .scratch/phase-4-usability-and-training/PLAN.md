# Phase 4: make the app usable day to day, then train in it

Twelve tickets in five groups. The order is chosen so the things that
hurt every single day land first and the two large pieces - rotated
boxes and in-app training - come after the app is pleasant to use.

## The constraint that shapes everything

**The disk is 99% full: 9.4 GB free of 476 GB.**

That is not a footnote. `day_anpr_gantry.mp4` is 90,003 frames, and
"keep every frame that has a vehicle in it" means up to 90,003 frame
rows and a crop written per detection per frame. At even 40 KB a crop
with two vehicles a frame, that is over 7 GB - and materialising the
full frames for labelling would be 18-36 GB on top. It does not fit.

So ticket 07 is not only "stop sampling". It is "stop writing pixels we
have not been asked for", and it blocks the whole every-frame idea.
This session has already had one near-miss: a selection trial wrote
1.2 GB of orphan JPEGs before it was caught.

## Groups, in order

**A. Clean up what is already broken (01-03).** There is no way to
remove a source, a finished job, or a frame. Your database currently
holds two dead RTSP sources, five cancelled or failed jobs, and a
90,003-frame video with nothing extracted. Small tickets, immediate
relief, and 01 reuses the interlock pattern the project delete just
established.

**B. Make the app source-aware (04-05).** Today the labelling queue
mixes every source's frames together, which is the thing you said makes
it unusable. Also the navigation buttons belong under the image.

**C. Detection you control (06-07).** Pick the model before detecting,
and keep every frame with a vehicle rather than a 5fps sample - within
the disk budget above.

**D. Label properly (08).** Rotate and resize a box. This one has a
decision in it; see the ticket.

**E. Train in the app (09-12).** Import datasets, merge them, train one
model at a time with progress, and continue from your own checkpoint.

## What I would cut if we need it sooner

09 and 10 (dataset import and merge) are separable from 11 (training).
Training the dataset this app already exports is useful on its own, and
import/merge only matters once you have data from elsewhere to bring in.

## What is not in scope here

Nothing changes about the exported label format until ticket 08 decides
the rotated-box question, and nothing about evaluation or active
learning. Those stay as they are.

## Reality check on data volume

Your latest export is 20 images, 21 boxes, one class. Twenty images
will not train anything. The point of group C is to make it cheap to
produce thousands of candidate frames, and of group B to make labelling
them bearable. Training (group E) is worth building, but it will not
produce a useful model until the labelling has caught up.
