# 06: Choose which model detects

**What to build:** Before running Detect + Track, pick the model. For
now that is two: the small YOLO and the slightly larger one.

The detector is a single hard-coded `yolo26n.pt` behind an `lru_cache`,
so there is no way to try a bigger model on a hard clip - or, later, to
use a model you trained yourself.

**Blocked by:** nothing

**Status:** done

- [x] `GET /models` lists what can be used: the built-in `n` and `s` weights, and any custom checkpoint in the models directory
- [x] The detect request takes a model id, and the processing run records which model produced its detections
- [x] The Sources panel offers the choice next to Detect + Track, defaulting to the last one used
- [x] Weights that are not present are fetched once and cached in the models directory, with the download reported as job progress rather than a silent stall
- [x] The detector cache is keyed by model, so switching does not reload the one already in memory
- [x] A live RTSP session takes the same choice - the stream is started with a named model, including a custom one you trained, rather than the hard-coded default
- [x] The evaluation report says which model a run used, since comparing two runs is the point of choosing

**Notes:**

- A model id becomes a file path, so it is checked before it gets near
  one: no separators, no `..`. A bad id is its own error from a typo,
  which is a 404.
- An unknown model is refused at submission rather than in the worker.
  Answering a typo two minutes later through a failed job is a poor
  way to report a typo.
- An unknown model is never quietly replaced by the default either -
  that is how two runs become incomparable, which defeats the point of
  choosing.
- Weights are fetched into *our* models directory rather than wherever
  ultralytics would put them, and the wait is reported as job progress.
  A custom model is never fetched: there is nowhere to fetch it from,
  and inventing a download turns a clear "it is not there" into a
  network error.
- `detector_version` now holds the model id rather than a path. A path
  says where the file was that day; the id says which model ran, which
  is what two reports are compared on.
- The detector cache is keyed by model id, so switching to the larger
  model and back does not reload the first one.
- The RTSP endpoint takes a provider rather than a detector, because
  the model is not known until the request body is read. Tests inject
  a stub through the same seam.
- Both pickers were exercised against the running app: the note and
  the "not downloaded yet" line change with the selection, and the
  choice survives a remount.
