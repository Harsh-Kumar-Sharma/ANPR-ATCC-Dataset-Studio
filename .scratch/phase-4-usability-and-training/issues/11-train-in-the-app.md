# 11: Train a YOLO model inside the app

**What to build:** Start a training run from a dataset version and
watch its progress, without leaving the app or copying a command into a
terminal.

**Blocked by:** 06 (Choose which model detects), 10 (Merge datasets)

**Status:** ready-for-agent

Today the app writes a `data.yaml` and a `RETRAINING.md` and hands off.
The job machinery for this already exists - `train` is a reserved job
type with no handler, and detection already proves the detached-process
pattern, the progress file and the cancel path.

**One at a time, deliberately.** The GPU is small and training is the
one job that will saturate it. A second run started by accident would
make both slower and might exhaust GPU memory outright.

- [ ] A `train` job handler runs Ultralytics training in a detached process, the way detection does
- [ ] Only one training job runs at a time; a second is refused with a clear reason naming the one already running
- [ ] Progress reports epoch, loss and the validation metric as they arrive, not just a spinner
- [ ] Cancelling stops the run and leaves the checkpoints written so far
- [ ] The trained weights land in the models directory and appear in the model list from ticket 06
- [ ] A training run records its dataset version, base model and settings, so a model can be traced to the data that made it
- [ ] Training survives the app being closed and reattaches on reopen, as detection already does
