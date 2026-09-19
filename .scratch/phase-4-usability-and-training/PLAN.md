# Phase 4: make the app usable day to day, then train in it

Twelve tickets in five groups. The order is chosen so the things that
hurt every single day land first and the two large pieces - rotated
boxes and in-app training - come after the app is pleasant to use.

## Storage is yours to manage, but the app has to help

The disk is 99% full: 9.4 GB free of 476 GB, and
`day_anpr_gantry.mp4` is 90,003 frames. Keeping every frame with a
vehicle in it means thousands of crops and, if they are materialised
for labelling, gigabytes of full frames.

You have said you will manage space yourself. So the app's job is not
to refuse - it is to make space visible and freeable at any moment,
including while a training run is going. That is ticket 13, and it
lands early rather than last.

Two things still change in the pipeline, because they cost nothing and
remove the worst of the waste: crops are decoded on demand instead of
written eagerly during detection, and a long run reports what it is
consuming as it goes.

## Groups, in order

**A. Clean up what is already broken (01-03, 13).** There is no way to
remove a source, a finished job, or a frame. Your database currently
holds two dead RTSP sources, five cancelled or failed jobs, and a
90,003-frame video with nothing extracted. Small tickets, immediate
relief, and 01 reuses the interlock pattern the project delete just
established. Ticket 13 belongs here too: freeing space has to be
possible before the big runs, not after they fail.

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

## Configurable, not decided for you

Every model choice in this plan is a choice you make at the moment you
act, not a setting buried once: which model detects a video, which
model runs a live stream, which model a training run starts from, which
datasets get merged into it. The defaults remember what you used last.
