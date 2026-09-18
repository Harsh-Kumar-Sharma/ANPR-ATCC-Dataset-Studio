# Benchmarks

Reproduce with `backend/scripts/benchmark_detection.py`. Numbers are for
one machine and are useful as ratios, not as absolutes.

## Detection throughput: CPU vs GPU

Ticket 01 (Phase 0 of `docs/REPLAN.md`). Machine: GTX 1650 with Max-Q
(4 GB, compute capability 7.5), driver 596.52, Python 3.14,
torch 2.14.0+cu132. Model `yolo26n.pt`, source `atcc1.mp4`,
150 frames after a 10-frame warm-up.

| Device | Throughput | Per frame (mean / median) | Detections |
|---|---|---|---|
| `cpu` | 31.6 fps | 31.7 ms / 31.5 ms | 132 |
| `cuda:0` | 63.9 fps | 15.6 ms / 14.7 ms | 132 |

**2.02x faster on the GPU**, with an identical detection count — the
speedup is not the GPU quietly finding less.

### Reading this honestly

No baseline was recorded against the old `2.14.0+cpu` build before it
was replaced, so the CPU row above is the *CUDA* build forced onto the
CPU with `--device cpu`. That holds the torch build constant and varies
only the device, which is the more meaningful comparison, but it is not
literally the wheel the project was running before.

2x is also a smaller win than a GPU usually suggests, and that is
expected here: `yolo26n` is tiny, so at 640px a single frame barely
saturates a Max-Q 1650, and per-frame Python and decode overhead is a
real share of the total. The win should widen with larger inputs,
batching, and especially training, which is what Phase 0 exists to
unblock.
