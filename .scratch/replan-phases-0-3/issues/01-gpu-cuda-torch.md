# 01: Put torch on CUDA and make device selection explicit

**What to build:** Detection and tracking run on the GTX 1650 instead of the CPU. A user starting a detect+track run over an existing video sees it finish measurably faster than the current CPU baseline, and the app reports a CUDA device rather than silently falling back.

The installed torch is a `+cpu` build, so nothing is using the GPU today. Every phase after this one assumes a training run takes hours rather than days.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] `torch.cuda.is_available()` is True inside the app's own virtualenv, not just a shell
- [ ] torch and torchvision are pinned in the backend project config (both are currently unpinned)
- [ ] The YOLO detector selects its device explicitly instead of relying on a library default, and logs which device it chose
- [ ] A detect+track run over `atcc1.mp4` is timed against the CPU baseline and the improvement is written down
- [ ] If no CUDA device is present, the app falls back to CPU with a visible warning rather than crashing
