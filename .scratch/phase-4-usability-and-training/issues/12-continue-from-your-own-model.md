# 12: Continue training from your own model

**What to build:** Train starting from a checkpoint you produced
earlier rather than from the pretrained base, over old and new data
merged together.

**Blocked by:** 11 (Train a YOLO model inside the app)

**Status:** ready-for-agent

This is the loop the whole app exists to serve: label, train, detect
with what you trained, label the frames it got wrong, train again.

- [ ] Any model in the models directory, including one this app trained, can be the starting point for a training run
- [ ] The detection model picker from ticket 06 offers custom checkpoints too, so a trained model can be used to pre-annotate the next batch
- [ ] A training run started from a custom checkpoint records its parent, so the chain back to the original base model is visible
- [ ] Old and new dataset versions merged with ticket 10 can be the training set in one action
- [ ] The evaluation report can compare a run detected with the custom model against one detected with the base
