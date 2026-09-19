# 12: Continue training from your own model

**What to build:** Train starting from a checkpoint you produced
earlier rather than from the pretrained base, over old and new data
merged together.

**Blocked by:** 11 (Train a YOLO model inside the app)

**Status:** done

This is the loop the whole app exists to serve: label, train, detect
with what you trained, label the frames it got wrong, train again.

- [x] Any model in the models directory, including one this app trained, can be the starting point for a training run
- [x] The detection model picker from ticket 06 offers custom checkpoints too, so a trained model can be used to pre-annotate the next batch
- [x] A training run started from a custom checkpoint records its parent, so the chain back to the original base model is visible
- [x] Old and new dataset versions merged with ticket 10 can be the training set in one action
- [x] The evaluation report can compare a run detected with the custom model against one detected with the base

**Notes:**

- Delivered with 11 rather than after it: "which model do we start
  from" is one parameter, and leaving it out would have meant
  shipping a training feature that could only ever train from
  scratch.
- Lineage is the `base_model_id` on each run. Following the chain
  back is reading rows, no extra table.
- The detection picker already offered custom models from ticket 06,
  so a model trained here can pre-annotate the next batch with no
  further work.

**Not done:**

- Merging several dataset versions into one training set. That is
  ticket 10, which is not built - training takes one version.
- Comparing two evaluation reports side by side. The reports each
  name their model (ticket 06), so the comparison is possible by
  reading both, but nothing puts them next to each other.
