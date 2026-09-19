# 11: Train a YOLO model inside the app

**What to build:** Start a training run from a dataset version and
watch its progress, without leaving the app or copying a command into a
terminal.

**Blocked by:** 06 (Choose which model detects), 10 (Merge datasets)

**Status:** done

Today the app writes a `data.yaml` and a `RETRAINING.md` and hands off.
The job machinery for this already exists - `train` is a reserved job
type with no handler, and detection already proves the detached-process
pattern, the progress file and the cancel path.

**One at a time, deliberately.** The GPU is small and training is the
one job that will saturate it. A second run started by accident would
make both slower and might exhaust GPU memory outright.

- [x] A `train` job handler runs Ultralytics training in a detached process, the way detection does
- [x] Only one training job runs at a time; a second is refused with a clear reason naming the one already running
- [x] Progress reports epoch, loss and the validation metric as they arrive, not just a spinner
- [x] Cancelling stops the run and leaves the checkpoints written so far
- [x] The trained weights land in the models directory and appear in the model list from ticket 06
- [x] A training run records its dataset version, base model and settings, so a model can be traced to the data that made it
- [x] Training survives the app being closed and reattaches on reopen, as detection already does

**Notes:**

- Runs on the job machinery detection already uses: a detached
  process, a progress file and a cancel. Training survives the app
  closing for the same reason detection does, and is reconciled on
  reopen by the same code.
- One at a time is global, not per project: this machine has one GPU,
  not one per project. The refusal names the run already going and
  says what to do about it.
- Everything checkable is checked at submission - the dataset export,
  the base model, the disk. Finding out an hour later through a
  failed job that a dataset was never exported is a poor way to learn
  it.
- `data.yaml` is written by training if the export does not have one.
  It used to come only from the handoff endpoint, which exists for
  training *outside* the app; requiring that button first would have
  been a trap.
- A failed or cancelled run is settled, because a row that claims to
  be training holds the GPU against every future run.
- Progress is written to the row as well as the progress file: the
  file is how the UI follows a live run, the row is what survives the
  app being closed and reopened.
- The progress bar is capped below 100% until the weights are
  imported. A bar at 100% with work still happening is a bar that
  lies.
- The trained model is named after what made it (`yolo26n-v3`) rather
  than `best.pt`, and a second run over the same pair does not
  overwrite the first.

**Not done as written:**

- No real training run has been executed. Ultralytics is replaced in
  every test, so what is proven is the machinery around it - what is
  refused, what is recorded, what happens on failure and cancel. The
  call itself was checked against the installed ultralytics for
  shape only: `add_callback` exists, `on_fit_epoch_end` is a real
  hook, `train(**kwargs)` accepts what is passed.
