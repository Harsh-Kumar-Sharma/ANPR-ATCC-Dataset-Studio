# 13 --- Labeling + In-App Training Plan

Plan for closing the full custom-model loop inside this app:

    label -> train -> download -> detect on new footage -> verify
      -> fix wrong detections -> retrain -> compare -> promote to production

This extends `docs/02_IMPLEMENTATION_PLAN.md` (Phases 0-9, all
complete) with Phases 10-17. It covers roadmap V1.5 ("training
launcher, experiment registry, evaluation dashboard") plus the CVAT-style
labeling workspace, which is not currently on the roadmap at all.

## Why this is not a small feature

Three things in the current build block this loop. They are not bugs -
they are correct decisions for a *review* tool that become wrong for a
*training* tool. All three must be addressed before any training code
is worth writing.

### Blocker 1 --- Only crops are stored, never full frames

`app/services/track_processor.py` crops each detection out of the frame
and saves only the crop (`cv2.imwrite(image_path, obs.crop)`). The full
frame is decoded, used, and discarded.

`app/services/dataset_export.py` therefore exports **one cropped image
per track, with one label box that fills nearly the entire image**.

A detector trained on that data learns "a vehicle always fills the whole
image." Fed a real 1920x1080 gantry frame where a car occupies 4% of the
pixels, it will perform badly. This dataset shape cannot train the model
the user actually wants, no matter how good the labels are.

**Mitigating factor:** the source video is copied into the project
workspace at import and never deleted, and every `frame_candidate` row
records `frame_index` and `timestamp_ms`. So full frames are fully
*recoverable* by re-decoding the retained source - no data was lost, and
no re-shooting of footage is needed.

### Blocker 2 --- One annotation per track, not many boxes per frame

`app/db/models/annotation.py` stores at most one active human annotation
per track ("editing which frame/class/bbox is chosen updates that same
row"). The review UI is built around judging one vehicle at a time.

CVAT-style labeling requires the inverse: one image, N objects, each with
its own box and class, added/edited/deleted independently. This needs a
new table and a new UI surface - it is not a modification of the existing
review screen.

### Blocker 3 --- PyTorch is a CPU-only build

Measured on this machine:

    torch 2.14.0+cpu      cuda available: False      device count: 0
    GPU present: NVIDIA GeForce GTX 1650 with Max-Q Design, 4096 MiB, driver 596.52

The GPU exists and is usable. The installed PyTorch simply cannot see it.
Training on the current CPU build is impractical (see "Hardware reality"
below). Installing a CUDA build of torch is a prerequisite for Phase 12,
not an optimization.

## Hardware reality (GTX 1650 Max-Q, 4 GB)

This constrains the "model size selection" feature directly - the UI
should reflect these limits rather than offering choices that will fail.

| Model | 640px feasibility on 4 GB | Notes |
| --- | --- | --- |
| `yolo11n` / `yolo26n` | Comfortable, batch 8-16 | Recommended default |
| `yolo11s` / `yolo26s` | Workable, batch 4-8 | Best accuracy/effort tradeoff here |
| `yolo11m` | Borderline, batch 2-4 | Likely OOM at higher batch/imgsz |
| `yolo11l` / `yolo11x` | Not viable | Do not offer, or offer disabled with a reason |

Max-Q is a thermally limited laptop part, so sustained throughput is
below a desktop 1650.

**Rough training-time estimates** (estimates, not measurements - the
first real run should replace these numbers in this document):

-   `yolo11n`, 640px, ~500 images, 100 epochs, CUDA: roughly 30-60 min.
-   `yolo11s`, same settings, CUDA: roughly 1-2 hours.
-   Same run on the current CPU-only build: expect 10-20x slower, i.e.
    overnight at best. Measured reference point: plain *inference* on
    CPU already took 104-166s for ~780 frames of the test clip
    (`docs/HANDOFF.md`), and training is far heavier per image.

**Implication for the UI:** training is a long-running background job
with progress, cancel, and resume - never a blocking request.

### Carry forward the lesson already learned

`POST /sources/{id}/process` ran a 1-3 minute pipeline synchronously
inside one open SQLite write transaction, which produced a raw
`database is locked` crash on any concurrent write (see
`docs/HANDOFF.md`). A training run is 30-100x longer. Training **must**
run as a separate background process that writes progress in short,
committed transactions. Repeating the synchronous pattern here would
lock the database for hours.

## Phase 10 --- Full-Frame Foundation --- DONE (2026-09-11)

The unblocker. Nothing else in this plan is worth building first.

**Shipped.** Decisions D-A and D-C's storage half are recorded as D-007
and D-008 in `docs/DECISIONS.md`. Verified on the real 3gp gantry clip:
exported images are now 1920x1080 full frames with box areas of 5.0%,
13.9% and 15.5% of the image (previously ~100% of a small crop), and a
label rendered back onto its frame lands correctly on the vehicle.

### Build

-   Persist full frames for sampled frames, not just crops.
-   Decide storage strategy (see "Open decisions" - lazy re-decode from
    the retained source video is the leading option).
-   New `frames` table: `source_id`, `frame_index`, `timestamp_ms`,
    `image_path` (or null if lazily decoded), `width`, `height`.
-   Link existing `frame_candidates` to their parent full frame.
-   Backfill path for existing projects by re-decoding retained sources
    at the recorded `frame_index` - no data loss, no re-import.
-   Rewrite `dataset_export.py` to emit full-frame images with **all**
    boxes for that frame in one label file.
-   Retire the crop-relative math in `yolo_export.py`
    (`bbox_relative_to_crop`) for the training path; keep the review UI's
    crop rendering unchanged.

### Definition of Done

An exported dataset version contains full-resolution frames, each label
file listing every labeled object in that frame in standard YOLO format,
and a sanity check confirms box areas are realistic fractions of the
image rather than ~100%.

### What Phase 10 revealed (feeds Phase 11)

Rendering an exported label back onto its frame showed the box correctly
placed on the annotated car - and also showed **several other real
vehicles in the same frame with no label at all** (another car, a
motorcycle, background traffic). Those are exported as background, which
actively teaches the model to ignore them.

The export warns about this, but only partially: it can count vehicles
the *detector* found and a human never accepted. A vehicle the detector
missed entirely is invisible to that check. Only human full-frame
labeling closes the gap, which settles open decision **D-C** - labeling
must cover the whole frame, not just frames that already have tracks.

## Phase 11 --- Labeling Workspace (CVAT-style)

### Build

-   Full-frame canvas with zoom, pan, and fit-to-window.
-   Draw a new box by dragging; move and resize via corner/edge handles;
    delete with a key.
-   Many objects per frame, each independently classed.
-   Class picker over the existing 20-class ATCC schema, with number-key
    hotkeys for the frequent classes.
-   **Prefill boxes from model predictions** so the task is "correct the
    model," not "label from scratch" - this is the same interaction the
    user described as fixing wrong detections, and it is what makes the
    loop economical.
-   Per-frame label status: `unlabeled` / `in_progress` / `done` /
    `skipped`.
-   Keyboard-first frame navigation, undo/redo, autosave.
-   New `frame_labels` table: `frame_id`, `class_id`, `bbox_json`,
    `source` (`model` | `human`), `created_at`, `updated_at` - keeping
    decision D-003 (human truth wins) intact.

### Definition of Done

A human can open a full frame, correct the prefilled boxes, add missed
vehicles, and move on in seconds per frame, with every box persisted and
re-editable.

## Phase 12 --- In-App Training Runs

### Build

-   Model picker: family (`yolo11`, `yolo26`), size (`n`/`s`/`m`), with
    infeasible sizes disabled and the VRAM reason shown.
-   Hyperparameters: epochs, `imgsz`, batch, patience, augmentation
    toggles - with defaults that fit 4 GB.
-   Start from a pretrained base **or** from a previous custom
    checkpoint (continue-training path).
-   Run training in a **separate background process** (subprocess, not a
    request thread), writing progress via short committed transactions.
-   Live progress: current epoch, box/cls losses, mAP50, mAP50-95,
    parsed from Ultralytics callbacks or `results.csv`.
-   Cancel a running job; survive an app restart (a run that was
    in-flight is reconciled on startup, not silently lost).
-   `training_runs` table: config, `dataset_version_id`, status, metrics,
    artifact paths, timing.
-   Document and detect the CUDA-vs-CPU torch situation; refuse to start
    a large CPU run without an explicit confirmation of the time cost.

### Definition of Done

The user selects a model and size, starts training from the app, watches
live epoch progress, and ends with a trained weights file on disk -
without the UI freezing or the database locking.

## Phase 13 --- Model Registry + Download

### Build

-   `model_versions` table: name, base model, `dataset_version_id`,
    `training_run_id`, metrics, weights path, created_at.
-   Registry UI: list every trained model with its metrics and lineage.
-   Download weights (`.pt`) at any time.
-   Optional ONNX export for production runtimes.
-   Mark one model as `active` for use inside the app.

### Definition of Done

Any model ever trained can be listed, compared, and downloaded on demand,
with traceable lineage back to the exact dataset version and labeled
frames it came from - preserving the roadmap's lineage invariant.

## Phase 14 --- Detect With a Custom Model

### Build

-   Model selection when starting Detect+Track on a video or RTSP stream.
-   Make the detector factory model-aware: `get_default_detector()` in
    `app/ml/factory.py` is currently `@lru_cache`'d to a single default -
    it needs a cache keyed by weights path, with a bounded number of
    loaded models.
-   Record which `model_version_id` produced each `processing_run`, so
    every detection is attributable to a specific model.
-   **Class-space change:** the stock base model emits COCO classes
    (`car`, `truck`, `bus`), while a custom model emits the 20 ATCC
    classes directly. Downstream code that assumes COCO names must
    handle both, driven by the model's own class list.

### Definition of Done

The user picks "my trained v3", processes a fresh video with it, and the
resulting tracks carry ATCC classes predicted by that model, recorded
against that model version.

## Phase 15 --- Correction Loop Closure

### Build

-   Review a custom model's detections on new footage (reuses the
    existing review UI).
-   A corrected box or class writes back into `frame_labels` as new
    human-truth training data.
-   Add missed vehicles (false negatives) by drawing boxes on the full
    frame - the case the current track-centric review cannot express at
    all, since a missed vehicle has no track.
-   Prioritized correction queue reusing Phase 8 active learning:
    low-confidence detections and model-vs-human disagreements first.
-   Build the next dataset version as previous labels plus new
    corrections, with no manual file handling.

### Definition of Done

Corrections made while reviewing real footage flow into the next dataset
version and the next training run automatically.

## Phase 16 --- Model Comparison ("is it good enough yet?")

This phase answers the user's stopping criterion directly: *keep going
until the custom model performs best.*

### Build

-   A held-out validation set that stays **fixed** across model versions,
    built on the existing frozen-validation-clip mechanism (Phase 7).
-   Per-version metrics: mAP50, mAP50-95, per-class AP, recall.
-   Version-over-version comparison table and chart.
-   Per-class regression warnings (e.g. v4 beats v3 overall but is worse
    on `Bus 3-Axle`).
-   A promotion gate: a model can be marked production-ready only when it
    beats the current best on the frozen set.

### Definition of Done

The user can objectively answer "is v4 better than v3, and where is it
worse" from one screen, rather than judging by eye.

## Phase 17 --- Production Handoff

### Build

-   Export a self-contained bundle: weights (`.pt` and/or ONNX), class
    names, preprocessing/inference config, and a model card recording
    lineage and metrics.
-   A minimal inference snippet for running the model outside this app.
-   Optional TensorRT export path (hardware-specific, best-effort).

### Definition of Done

A downloaded bundle runs in a production environment with no dependency
on this app or its database.

## Data model changes at a glance

| Table | Status | Purpose |
| --- | --- | --- |
| `frames` | New (Phase 10) | Full-frame records with recoverable decode info |
| `frame_labels` | New (Phase 11) | Many boxes per frame, human or model sourced |
| `training_runs` | New (Phase 12) | Training config, progress, metrics, artifacts |
| `model_versions` | New (Phase 13) | Trained model registry + lineage |
| `annotations` | Unchanged | Existing track-level review keeps working |
| `processing_runs` | Extended (Phase 14) | Gains `model_version_id` |

The existing `source -> track -> evidence -> annotation -> dataset
version` lineage is preserved; this plan extends it with
`-> model version -> evaluation`, matching the roadmap's long-term
invariant.

## Open decisions

These need an answer before the phases they affect are built. They are
listed here rather than silently assumed.

-   **D-A: Full-frame storage strategy** (blocks Phase 10). Store every
    sampled full frame to disk (simple, but roughly 160-310 MB per
    156-second clip at 5 fps and 1080p JPEG), or re-decode on demand from
    the retained source video (near-zero storage, slower labeling, and
    needs a cache). A hybrid - persist only frames that get labeled -
    is likely the right answer.
-   **D-B: Which YOLO families to offer** (blocks Phase 12). Restricting
    to `yolo11` + `yolo26` at sizes `n`/`s`/`m` keeps the picker honest
    on 4 GB. Offering everything invites OOM failures.
-   **D-C: Label the whole frame, or only frames with tracks?** Full-frame
    labeling teaches the model about true negatives and missed vehicles;
    track-only labeling is faster but perpetuates the model's blind spots.
-   **D-D: What counts as "best performing"** (blocks Phase 16). mAP50-95
    on the frozen set is the usual default, but recall may matter more
    for a counting/ANPR use case where a missed vehicle is worse than a
    slightly loose box.

## Suggested build order

Phases 10 and 11 are prerequisites for everything else and carry the
most work. Phase 12 is the highest-risk phase (long-running jobs,
GPU memory, process lifecycle). Sizes below are relative effort, not
calendar estimates.

1.  **Phase 10** - Full-frame foundation. Large. Unblocks all.
2.  **Phase 11** - Labeling workspace. Large. The main new UI surface.
3.  **Phase 12** - Training runs. Large, highest risk.
4.  **Phase 13** - Model registry + download. Medium.
5.  **Phase 14** - Detect with custom model. Medium.
6.  **Phase 15** - Correction loop closure. Medium.
7.  **Phase 16** - Model comparison. Medium.
8.  **Phase 17** - Production handoff. Small.

A useful intermediate milestone lands after Phase 13: at that point the
user can label, train, and download a model, even before the app can use
it internally. Phases 14-16 are what make the loop iterative rather than
one-shot.

## Prerequisite before Phase 12

Install a CUDA build of PyTorch matching the installed driver (596.52,
GTX 1650, 4 GB), replacing the current `2.14.0+cpu` build, and confirm
`torch.cuda.is_available()` returns `True`. Without this, in-app training
is technically functional but too slow to iterate on.
