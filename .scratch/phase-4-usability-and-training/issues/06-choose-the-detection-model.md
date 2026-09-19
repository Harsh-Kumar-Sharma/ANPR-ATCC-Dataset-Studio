# 06: Choose which model detects

**What to build:** Before running Detect + Track, pick the model. For
now that is two: the small YOLO and the slightly larger one.

The detector is a single hard-coded `yolo26n.pt` behind an `lru_cache`,
so there is no way to try a bigger model on a hard clip - or, later, to
use a model you trained yourself.

**Blocked by:** nothing

**Status:** ready-for-agent

- [ ] `GET /models` lists what can be used: the built-in `n` and `s` weights, and any custom checkpoint in the models directory
- [ ] The detect request takes a model id, and the processing run records which model produced its detections
- [ ] The Sources panel offers the choice next to Detect + Track, defaulting to the last one used
- [ ] Weights that are not present are fetched once and cached in the models directory, with the download reported as job progress rather than a silent stall
- [ ] The detector cache is keyed by model, so switching does not reload the one already in memory
- [ ] The evaluation report says which model a run used, since comparing two runs is the point of choosing
