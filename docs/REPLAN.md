# Replan: Dataset Studio → Training Studio

Status: agreed 2026-09-18. Supersedes the implicit roadmap in code comments.

## Why this document exists

The app today is a **dataset curation tool**. It ingests video, detects and tracks
vehicles, lets a human review tracks, and exports a YOLO dataset plus a
`RETRAINING.md` telling you to go run `yolo detect train` yourself. It stops
exactly where the interesting part begins.

The target is a **training studio**: the full loop — import, detect, label, train,
re-run the trained model, measure, retrain — happens inside the app, and ends with
a production-ready exported model.

The measure of success is not features shipped. It is: **how many times has the
loop gone around?** Today the answer is zero. The database has 5352 detected frame
candidates, 20 annotations, and no trained models — the existing subsystems are
each solid, but they were never joined into something that turns effort into a
better model.

## The one rule

Work proceeds **subsystem by subsystem** (deep focus on one area at a time), but a
subsystem is **not done when it is complete — it is done when it is connected**.

Concretely: the labeling canvas is not finished when every keyboard shortcut works.
It is finished when labels drawn in it have travelled all the way through export
into a training run. Ship the seam, then deepen.

This exists because the current codebase is what happens without it.

---

## What we keep, what we rewrite

### Keep as-is (genuinely good, no reason to touch)

| Area | Files |
|---|---|
| RTSP ingestion + reconnect | `backend/app/services/rtsp_source.py`, `rtsp_session.py`, `rtsp_session_registry.py` |
| Live preview | `desktop/src/components/LivePreview.tsx` |
| Video playback + overlay | `desktop/src/components/VideoPlayer.tsx` |
| Detection + tracking core | `backend/app/services/track_processor.py`, `backend/app/ml/bytetrack_tracker.py` |
| Frame quality scoring | `backend/app/services/frame_ranking.py`, `quality_signals.py` |
| Frame materialization | `backend/app/services/frame_materializer.py` |
| Dataset split logic | `backend/app/services/dataset_split.py` |
| Project create/select | `desktop/src/components/ProjectPicker.tsx`, `backend/app/api/projects.py` |
| Detector abstraction | `backend/app/ml/detector.py` (the Protocol — the implementation changes) |

### Rewrite

| What | Why |
|---|---|
| `backend/app/core/class_schema.py` | Hardcoded 20 ATCC classes → becomes a **presets** module; real classes live per-project in the DB |
| `backend/app/db/models/annotation.py` | FKs to `frame_candidate_id` only. Must hang off **`frame_id`** so one frame can hold many boxes |
| `backend/app/services/review.py` | `submit_review` enforces one annotation per track (`review.py:53-65`). Frame-level labeling makes this structurally wrong |
| `backend/app/ml/factory.py` | `@lru_cache`'d single hardcoded `yolo26n.pt` (`factory.py:13-31`) → loads whichever model the run selected |
| `backend/app/services/dataset_export.py` | Frame-grouping already exists (`:77-86`) but queries are track-keyed; also must emit crop-derived datasets for the plate model |
| `backend/app/services/dataset_query.py` | `query_approved_items` joins Annotation→FrameCandidate→Track→Run→Source (`:34-42`). Becomes a frame-level query |
| `desktop/src/components/TrackReview.tsx` | 4 numeric bbox inputs + read-only SVG (`:223-225`, `:262-278`). Replaced by the labeling canvas; the plate-text card (`:305-338`) survives into the attributes panel |

### Retire

- `backend/app/services/retraining_handoff.py` — writing a `RETRAINING.md` telling
  the user to go train elsewhere is the exact thing this replan deletes. The
  `data.yaml` generation inside it moves into the training service.

### Build new

Everything in Phases 1–9 below.

---

## Data model: before and after

**Before** — everything hangs off a track; a label is one box on one tracked vehicle:

```
Project → Source → ProcessingRun → Track → FrameCandidate → Annotation (0 or 1)
                                                ↓
                                              Frame
FrameCandidate → OcrCandidate
DatasetVersion → DatasetItem → Annotation
```

**After** — the frame is the unit of labeling; tracks become a selection aid:

```
Project → ClassDefinition (project-owned, seeded from a preset)
Project → Source → ProcessingRun → Track          (dedup / frame selection only)
                                 → Frame ← Annotation (many)
Project → Model → TrainingRun → DatasetVersion (immutable snapshot)
Job (detect | train | export | preannotate)
```

Key field changes:

- `Annotation`: `frame_candidate_id` → **`frame_id`**; add **`attributes` (JSON)**
  — plate text, vehicle color, direction, occluded, night. One generic field so new
  attributes never need a migration.
- `Frame`: add **`status`** (`pending` / `labeled` / `rejected`) and
  **`selection_reason`** (why this frame entered the queue — debuggability).
- `Track`: keeps `bucket`; `review_status` loses its labeling meaning.
- `OcrCandidate`: stays, but demoted to **model prediction / pre-annotation source**.
  Human-corrected plate text lives in `Annotation.attributes`.

New tables: `ClassDefinition`, `Model`, `TrainingRun`, `Job`.

---

## Phases

Sizes are rough and assume solo work: **S** ≈ 1–2 days, **M** ≈ 3–5 days,
**L** ≈ 1–2 weeks, **XL** ≈ 2–3 weeks. Treat them as relative, not as promises.

### Phase 0 — GPU · **S**

The machine has a GTX 1650 (4 GB) but the installed torch is `2.14.0+cpu`. Nothing
is using the GPU today. Everything downstream is built on the assumption that a
training run takes hours, not days.

```
backend/.venv/Scripts/python.exe -m pip uninstall -y torch torchvision
backend/.venv/Scripts/python.exe -m pip install torch==2.14.0 torchvision==0.29.0 --index-url https://download.pytorch.org/whl/cu132
```

- Pin torch + torchvision in `backend/pyproject.toml` (currently unpinned).
- Add explicit device selection to `backend/app/ml/yolo_detector.py`.

**Done when:** an existing detect+track run over `atcc1.mp4` is measurably faster
than the CPU baseline, and `torch.cuda.is_available()` is True in the app's env.

### Phase 1 — Job system · **M**

Detection currently runs synchronously inside the HTTP request and blocks for 1–3
minutes with no progress and no cancel. Training needs hours. Both need the same
machinery, so build it once.

New:
- `backend/app/db/models/job.py` — type, status, progress, pid, timestamps, error
- `backend/app/services/jobs/runner.py` — **detached subprocess** launch, progress
  via a file or pipe, cancel, and **reattach on app restart** (agreed: training must
  survive closing the app)
- `backend/app/api/jobs.py` — list, get, cancel, stream progress
- `desktop/src/components/JobsPanel.tsx` + a persistent progress indicator

Changed: `process_source_endpoint` in `backend/app/api/sources.py` becomes a job
submission instead of a blocking call.

Concurrency: **one training job at a time**. A second request prompts the user to
cancel the running one or queue behind it. Detection jobs are not subject to this.

**Done when:** detection runs as a background job with live progress, you can close
the app mid-run, reopen it, and see the job still going.

### Phase 2 — Per-project classes · **M**

New:
- `backend/app/db/models/class_definition.py` — project-scoped classes
- `backend/app/core/presets.py` — ATCC (the current 20), ANPR (vehicle + plate), Blank
- `backend/app/api/classes.py` — CRUD, plus **remap-on-delete**
- `desktop/src/components/ClassSchemaEditor.tsx`

Rules: creating a project **copies** a preset into it — presets are never shared
live, so editing one project can't break another. Rename is always safe. Delete or
merge prompts *"these 47 labels use this class — move them where, or delete them?"*
Orphaned labels are the easiest way to silently poison a dataset.

Migration seeds every existing project with the ATCC v1 preset so nothing breaks.

**Done when:** the existing review UI reads classes from the project, and a class
renamed in the editor shows up there.

### Phase 3 — Frame-level labeling · **XL** ← the big one

This is a new subsystem, not a modification. There is **no drag-to-draw code
anywhere** in `desktop/src` today, and `review.submit_review` structurally forbids
a second box on a frame.

Backend:
- Migration: `Annotation.frame_id`, `Annotation.attributes`, `Frame.status`,
  `Frame.selection_reason`
- `backend/app/api/frames.py` — **does not exist today**. List the labeling queue,
  serve a frame image, save all annotations for a frame, reject a frame
- `backend/app/services/frame_selection.py` — which frames enter the queue:
  quality score (reuse `frame_ranking.py`) + **simple-heuristic diversity**
  (brightness, vehicle count, perceptual-hash near-duplicate removal). Uncertainty
  sampling arrives in Phase 8, once a custom model exists to be uncertain.
- Rewrite `dataset_query.py` and `dataset_export.py` onto frames

Frontend:
- `desktop/src/components/LabelCanvas.tsx` — from scratch. Draw, move, resize,
  delete boxes; per-box class; attributes panel; reject frame; keyboard-first
  (the existing shortcut handling in `TrackReview.tsx:165` is the only prior art)
- `desktop/src/components/LabelQueue.tsx` — queue navigation and progress

**Selection is track-level, labeling is frame-level.** If frame #1204 was chosen
because it is the best shot of track A, then vehicles B, C and D in that same frame
must also be labeled. Unlabeled objects teach the model "nothing here" — the bug
`dataset_export.py:167-174` already flags against itself.

**Done when:** 50 frames labeled in the canvas export as a valid YOLO dataset with
multiple boxes per image, and `dataset_validator.py` passes it.

### Phase 4 — Training · **L**

New:
- `backend/app/db/models/model.py` — id, project, **type** (`vehicle_detector` /
  `plate_detector` / `ocr`), base model, weights path, metrics, training run
- `backend/app/db/models/training_run.py` — config, dataset snapshot, status, metrics, log
- `backend/app/services/training/ultralytics_trainer.py` — runs as a Phase 1 job,
  parses progress out of Ultralytics output
- `backend/app/services/training/config.py` — presets + advanced override
- `desktop/src/components/TrainingPanel.tsx`, `ModelRegistry.tsx`

Config presets tuned for 4 GB VRAM — **nano @ 640, batch 8–16**; **small @ 640,
batch 4–8**; medium will OOM, do not offer it. Windows needs `workers=2`;
`batch=-1` autobatch is unreliable at this VRAM, set batch explicitly.

Dataset selection defaults to **cumulative** (every accepted label), but each run
writes an **immutable `DatasetVersion` snapshot**, so "what was model #4 trained on"
is always answerable. Advanced mode allows selecting specific versions.

`retraining_handoff.py` is deleted here; its `data.yaml` generation moves in.

**Done when:** labeled frames produce a trained `.pt` that appears in the registry
with its metrics.

### Phase 5 — Model selection + pipeline · **M** ← **the loop closes**

- `backend/app/ml/factory.py`: drop the `lru_cache`d singleton; load the model the
  run asks for, keyed by registry id
- `ProcessingRun`: add a pipeline config — which stages run, which model version at
  each stage. Defaults to the full chain; individually togglable so you can re-test
  a new plate model without re-running vehicle detection over 900 MB of video
- Model picker in `SourcePanel.tsx` and `RtspPanel.tsx`

**Done when:** you select your own trained model and re-run it on the video — the
loop has gone around once. Everything before this phase is setup; everything after
is making the loop better.

### Phase 6 — Pre-annotation · **M**

Model predictions become pre-filled boxes in the labeling queue
(`Annotation.source = 'model'`, `status = 'pending'`), which the human corrects
rather than draws from scratch.

This is the compounding step: iteration 1 you draw 100% of boxes, by iteration 4
you are correcting 10%. Without it every retrain cycle costs the same manual effort
and the loop stalls out after two or three rounds. `active_learning.py` is the
natural home.

**Done when:** a second labeling pass is measurably faster than the first.

### Phase 7 — Evaluation + production export · **L**

- mAP50 / mAP50-95 + per-class breakdown via Ultralytics val
- **New vs previous model** comparison — the actual "did this retrain help?" answer
- Frozen validation clip check (reuse `evaluation.py` and the existing
  `is_frozen` / `ground_truth_vehicle_count` concept)
- **ONNX / TensorRT export** + inference speed benchmark
- Rework `EvaluationPanel.tsx` around model versions rather than pipeline output

**Done when:** you can look at two models side by side and say which ships.

### Phase 8 — Plate detector (two-stage) · **L**

- `number_plate` class in the ANPR preset; plate boxes drawn in the same canvas on
  the **full frame**
- Crop-derived export: plate boxes are automatically converted into vehicle-crop
  coordinates at export time. **You never label on a crop.** One labeling pass
  produces both datasets
- Both full-frame and crop-based plate detection supported, configurable per run —
  on gantry footage a plate is ~25×10 px in a 1080p frame but ~80×35 px in a vehicle
  crop, which is the difference between trainable and not
- Second model type flows through the registry and the pipeline
- Uncertainty-based frame selection lands here (a custom model now exists)

**Done when:** plates are detected by your own model on a video run.

### Phase 9 — Custom OCR · **L**, and not before the data exists

**Today there are zero plate training labels**, and plate boxes in `OcrCandidate`
are crop-local with human corrections carrying a dummy `[0,0,0,0]` box
(`ocr_processor.py:99`). There is nothing to train on. Phases 5–8 collect the data
as a side effect of normal use; this phase turns it on.

Approach: **`fast-plate-ocr` fine-tune** (MIT, Keras 3 on the PyTorch backend,
pretrained plate checkpoints, exports ONNX — drops straight into the existing
ONNXRuntime inference path). Plate text is labeled by **typing a string**, which the
existing plate-text UI already does and which pre-annotation makes nearly free.

Rejected: **PaddleOCR fine-tuning is blocked** — `paddlepaddle` ships wheels for
cp39–cp313 only, and this project is on Python 3.14. It would require a second conda
env or WSL2, which defeats "in-app".

Fallback: **character-level YOLO** (36 classes, detect each character on the plate
crop, sort left-to-right). Reuses the entire existing pipeline and handles
double-line plates naturally, but costs ~10 boxes per plate to label — 5,000+ boxes
by hand. The infrastructure will exist by then, so this stays available if
`fast-plate-ocr` disappoints or its Python 3.14 wheels turn out to be a problem
(**unverified — check before committing to this phase**).

Honest numbers: under ~300 labeled plates a custom OCR will **lose** to stock
RapidOCR. Real gains start around 1,000–3,000. Indian specifics that matter:
double-line plates on trucks and two-wheelers, BH-series breaking the classic
`SS DD AA NNNN` format regex, colour carrying meaning (yellow commercial / white
private / green EV), and the HSRP hologram reading as spurious text.

**Done when:** your OCR beats RapidOCR on a held-out plate set. If it doesn't, keep
RapidOCR — that is a legitimate outcome, not a failure.

---

## Existing data

Preserve `atcc1.mp4` (900 MB) and the 3 projects. Re-run detection on GPU rather
than migrating `Track` / `FrameCandidate` rows. The 20 existing annotations are
worth ~10 minutes to recreate and are not worth a migration path.

The "accumulate all data" principle applies **going forward**, from Phase 3 onward.

## Risks

1. **Phase 3 is the schedule.** It is an XL from-scratch subsystem and everything
   after it depends on it. If it slips, consider shipping the canvas with
   draw/resize/delete + class only, deferring the attributes panel to Phase 8.
2. **4 GB VRAM is a real ceiling.** Nano and small models only. If accuracy plateaus
   below target, the constraint is hardware, not method — decide then whether to
   rent a GPU rather than fighting it in-app.
3. **Scope is large for one person.** Phases 0–5 close the loop; 6–9 improve it.
   If time runs short, stopping after Phase 7 leaves a genuinely useful tool.
   Stopping before Phase 5 leaves what exists today.
4. **`fast-plate-ocr` on Python 3.14 is unverified.** Check wheel availability
   before starting Phase 9, not during.
