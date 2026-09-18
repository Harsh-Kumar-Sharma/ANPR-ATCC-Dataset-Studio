# Development Handoff

This file is updated by Codex after every meaningful implementation
session.

## Session: Video Player With Detection Boxes + Live Stream Preview

User report: "started it but detection and video stream not show". Nothing
was broken - the app simply had no way to *watch* footage: Detect+Track
ran headless and only produced a track list, and the Live tab showed
counters only. Both views were added.

### What was built

-   **Video player** (`desktop/src/components/VideoPlayer.tsx`, opened
    with **Watch** on a video source). Plays the imported file natively
    via `GET /projects/{p}/sources/{s}/video` (`app/api/playback.py`,
    `FileResponse` with HTTP Range, no transcoding) and draws tracked
    boxes from `GET .../detections` (`services/detection_overlay.py`)
    as an SVG overlay synced with `requestVideoFrameCallback`. Timeline
    markers per vehicle, Prev/Next vehicle, speed, run selector (only one
    run is ever overlaid - re-processing would otherwise double every
    box), click a box to open that track for review.
-   **Live preview** (`LivePreview.tsx`, opens automatically on Start).
    The RTSP processing thread renders the latest processed frame with
    boxes burned in (`services/live_preview.py`), held in memory only -
    no DB writes. UI polls `GET /processing-runs/{run}/rtsp/preview.jpg`
    (204 until the first frame; `X-Frame-Sequence` exposed via CORS).
-   `observe_frame` now returns the frame's own tracked boxes;
    RTSP runs now set `completed_at` on finalize (pre-existing omission).

### Verified on the real 3gp gantry clip

-   Chromium decodes the H.264-in-3GP file natively (readyState 4,
    1920x1080, 155.9s). Box overlay alignment measured pixel-exact
    (drawn `[289,168,241,137]` == expected). Next vehicle, click-box ->
    review, and constant 13px labels all confirmed.
-   Live preview: 237 distinct server-drawn frames in 40s (~5.9/s),
    boxes correctly drawn on vehicles, UI badge 7.0 fps. Stop -> run
    `completed` with `completed_at`, CPU back to 0 cores.
-   Tests: backend 183 passed; desktop 6 passed (new
    `tests/VideoPlayer.test.tsx`); `tsc -b` clean.

### Bugs found while verifying (and fixed)

-   **Live preview froze for whole batches.** It was published once per
    drained batch; with detection slower than capture a batch spans
    hundreds of frames (measured: 1 new frame in 8s). Now published per
    processed frame, throttled. Regression test
    `test_live_preview_keeps_updating_while_a_backed_up_batch_is_processed`
    was run against the old loop too: old advanced 0 times, new 9.
-   **Labels ~8px on a small player** - font size was in video pixels;
    now converted from a fixed screen size via `ResizeObserver`.

### Known limitations / notes

-   A local file used as an "RTSP" URL is read unpaced (~224 fps), so
    most frames are dropped and the backend used ~10 cores. A real
    camera is paced by its own frame rate.
-   Stop finishes the frames already buffered first: measured 5.1s and
    15.7s. Deliberately not changed (it would discard captured frames).
-   No React error boundary: any render error in the player/preview
    blanks the whole app. Seen during dev when Vite's file watcher
    missed an `api.ts` write and kept serving a stale module (fixed by
    touching the file). Adding a boundary around the main panel is
    recommended.
-   Codecs the embedded Chromium can't decode show a message instead of
    playback; detections remain reviewable in the Tracks list.

## Phase 10 --- Full-Frame Foundation (2026-09-11)

First phase of `docs/13_LABELING_AND_TRAINING_PLAN.md`, the plan for
closing the label -> train -> deploy -> correct -> retrain loop in-app.
Phase 10 is the unblocker: until training data is full frames, nothing
downstream is worth building.

### The problem it fixes

The export produced **one cropped image per track, with a single box
covering ~100% of it**. A detector trained on that learns a vehicle
always fills the frame, and fails on real gantry footage where a vehicle
covers a few percent of the pixels. Crops were the only pixels ever
saved (`track_processor._persist_track`); full frames were decoded, used
for detection, and discarded.

### What changed

-   **`frames` table** (`db/models/frame.py`): identity of a sampled
    full frame - `source_id`, `frame_index`, `timestamp_ms`, size, and a
    **nullable** `image_path`. `frame_candidates.frame_id` links a crop
    to its parent frame.
-   **`services/frame_materializer.py`**: full frames are decoded from
    the retained source video **on demand** and cached, rather than
    written eagerly (~160-310 MB per clip, mostly for frames never
    used). One ordered `VideoCapture` pass per source, never one open
    per frame.
-   **`services/dataset_export.py`** rewritten to be frame-shaped: one
    image per frame, every accepted box for that frame in one label
    file, and **the split unit is now the frame, not the track** - two
    vehicles sharing a frame must not land in different splits, which
    would put the same pixels in train and val each missing a box.
-   **Backfill**: pre-Phase-10 `frame_candidates` have no `frame_id`,
    but they record `frame_index` against a source still in the
    workspace, so `_resolve_frame` creates the row on the fly. Old
    projects export correctly with no re-import.
-   **`services/dataset_validator.py`**: manifest items are frames with
    N objects; added a warning when a box covers >=98% of the image (the
    signature of the old crop export) and when a frame contains detected
    vehicles nobody accepted.
-   Migration `2eb8c1baa797`. Autogenerate emitted a bare
    `op.create_foreign_key`, which SQLite cannot execute - rewritten
    with `batch_alter_table`. Verified existing rows survived the
    table rebuild.

### Verified

-   `pytest`: 160 passing, including a new test that two vehicles in one
    frame export as **one image with two label lines in one split**, and
    one asserting exported images match the source dimensions with box
    area < 98%.
-   `tsc -b` + `vitest`: clean.
-   **On the real 3gp gantry clip**: exported images are 1920x1080 with
    box areas of 5.0% / 13.9% / 15.5%. Rendering a label back onto its
    frame put the box correctly on the car - position, not just size.
-   Live in the UI: export reports "v3: 3 full frame(s), validation
    passed."

### Concurrency: the write lock is still held for a whole run

Observed live while testing, not theorised. During a detect+track run
(a worker burning ~2100s of CPU), `GET /health` and every read kept
working, but **every write was blocked for the entire run** - both
`POST /projects` through the API and a direct `sqlite3` write. Both
recovered by themselves the moment the run finished.

So the earlier "database is locked" work is only partly done:

-   Fixed: duplicate `/process` calls now get a 409 instead of a crash.
-   Fixed: WAL keeps **reads** working during a run.
-   **Still open:** an unrelated **write** (create a project, save a
    review) blocks for the run's full duration, and the 30s
    `busy_timeout` is far shorter than a 2-6 minute run, so those
    callers still eventually see "database is locked".

The real fix is to stop holding one transaction across the whole
pipeline - commit incrementally, or move the run to a background
process. Phase 12 (training) must not repeat the pattern, since a
training run is 30-100x longer again.

### Known gaps (deliberate, feed later phases)

-   **RTSP frames are not covered.** Live capture has no re-decodable
    source, so those frames stay crop-only until they are persisted
    eagerly (recorded in D-008).
-   **Partial labeling is only partly detectable.** The warning counts
    vehicles the *detector* found and a human never accepted; a vehicle
    the detector missed entirely is invisible and still exported as
    background. Visual check on the real clip confirmed this happening -
    an unlabeled car and motorcycle in an exported frame. Only Phase 11
    full-frame labeling closes it.

## Post-Redesign Session: Real-Footage Test + Concurrent-Process Bug Fix

Direct user request to test with a real phone-camera clip
(`27_2026-08-31_212943.3gp`, 1920x1080 @ 8fps, ~156s), run through the
real pipeline end to end - actual YOLO26n detection, ByteTrack, and
RapidOCR, not the stub used for UI work. The pipeline worked correctly
(3 tracks found, correct bucketing, OCR ran and honestly reported the
camera's on-screen watermark text since this overhead gantry angle
doesn't show plates). While testing, a real bug surfaced.

### Bug: "database is locked" crash on a second Detect+Track click

`POST /sources/{id}/process` (`backend/app/api/sources.py`) runs the
entire detect+track pipeline synchronously inside one open SQLite
write transaction - on real footage this can hold the write lock for
1-3 minutes (166s and 104s observed on this clip on CPU). Any other
write that lands during that window - a second click, a reload
+ re-click, or a second project/tab - crashed instantly with a raw
`sqlite3.OperationalError: database is locked` (uncaught 500) instead
of a useful message, because `backend/app/db/session.py`'s SQLite
engine had no busy timeout and used the default rollback-journal mode.
Confirmed via the DB directly (`processing_runs` table): the original
request always completed successfully in the background - only the
impatient retry crashed - so no data was ever lost, but the error was
alarming and unexplained.

### Fix

-   **`backend/app/db/session.py`**: SQLite connections now open with
    `PRAGMA journal_mode=WAL` and a 30s `busy_timeout` (both via
    `connect_args={"timeout": 30}` and an explicit `PRAGMA`). WAL lets
    reads proceed while a write is in flight and makes a second writer
    queue instead of failing immediately - general defense-in-depth
    for every endpoint, not just `/process`.
-   **`backend/app/api/sources.py`**: `process_source_endpoint` now
    checks for an existing `ProcessingRun` with `status="running"` on
    the same source before starting, and raises a new `ConflictError`
    (409, `core/errors.py`) with a clear message instead of reaching
    the DB layer at all. `GET /sources` now also returns a computed
    `is_processing` flag per source (true while any run for it is
    `status="running"`).
-   **`desktop/src/components/SourcePanel.tsx`**: the Detect+Track
    button now reflects `is_processing` from the server (not just this
    tab's own click state), and polls every 3s while any source is
    processing so the button stays accurate across a reload or a
    second window - closing the exact gap that caused the crash.

### Verified

-   `pytest` (158 tests, all passing) - added
    `test_process_source_conflicts_when_a_run_is_already_in_progress`,
    which inserts a `running` `ProcessingRun` row directly and asserts
    the endpoint now returns 409 `processing_already_running` and
    `GET /sources` reports `is_processing: true`.
-   `tsc -b` and `vitest run` - clean.
-   Live-verified in the browser: simulated an in-progress run by
    inserting a `running` row directly into `data/app.db`, confirmed
    the Sources panel shows a disabled "Processing..." button purely
    from server state on a fresh page load (no local click state
    involved), confirmed the disabled button blocks the click
    entirely, then deleted the row and confirmed the button
    re-enabled itself within one 3s poll cycle with no reload needed.
-   Not covered: an automated test that drives two real concurrent
    HTTP requests against the synchronous endpoint (the unit test
    simulates the "already running" state directly instead, which
    exercises the same guard without needing a slow real detector run
    in CI).

## Post-Build Session: UI/UX Redesign

Direct user request ("make ui and ux better"). A live design review
before starting (stub-detector backend + Vite dev server driven through
the browser tool) found the app functionally complete but visually
unstyled: default-HTML look, no spacing/typography system, all 6 sidebar
panels stacked in one long column causing a double-scrollbar overflow
bug, and - most importantly - the Track Review screen (the single
most-used screen) rendering its frame image tiny and cramped. This
session was a full design-system rewrite, not piecemeal CSS tweaks.

### What changed

-   **`desktop/src/index.css`** rewritten from scratch as a token-based
    design system: light-theme tokens on `:root`, dark overrides via
    `@media (prefers-color-scheme: dark)` (matches the existing
    `color-scheme: light dark` pattern). Tokens for background layers,
    borders, text, accent, success/warning/danger, radii, shadows, and
    fonts, plus component classes for buttons, badges, cards, and every
    panel's specific layout.
-   **`desktop/src/Icons.tsx`** (new): small inline SVG icon set
    (folder, film, broadcast, box, chart, arrow-left, play, check,
    alert, x, inbox) - chosen over an icon library dependency since the
    set needed is small and fixed.
-   **`desktop/src/App.tsx`**: replaced the single vertical stack of all
    6 panels with a **tab-based sidebar** (Sources / Live / Dataset /
    Insights). This directly fixes the double-scrollbar/overflow bug.
    Selecting a track from an Insights-tab queue (Active Learning,
    disagreements) now switches back to the Sources tab so the review
    workspace is actually visible.
-   **Every sidebar component** (`ProjectPicker`, `SourcePanel`,
    `TrackBrowser`, `RtspPanel`, `DatasetPanel`, `EvaluationPanel`,
    `ActiveLearningPanel`) rewritten to use the new tokens/classes:
    section headers with icons, `.card`-wrapped stat blocks, semantic
    badges (`bucket-*`, `review-*`), consistent button variants
    (`btn-primary`/`btn-ghost`/`btn-danger-ghost`).
-   **`TrackReview.tsx`** (the main workspace, redesigned last): large
    prominent image viewer (`.frame-image-wrap`, checkered-transparency
    background for the cropped frame, up to 480px tall) with the bbox
    overlay drawn as an SVG `<rect>`, a horizontal filmstrip of
    thumbnails, and the review controls (class picker, numeric bbox
    editor, accept/hard/failed actions, OCR panel) moved into cards on
    the right in a responsive 2-column grid that collapses to 1 column
    under 900px. Keyboard shortcuts are now shown as `.kbd` pill tags
    instead of plain text. The bbox editor stayed numeric-input-only
    (no drag-to-resize on the image) - out of scope for a visual
    redesign pass.

### Verified

-   `npx tsc -b` in `desktop/` - clean, no errors.
-   `npx vitest run` in `desktop/` - `tests/App.test.tsx` still passes
    against the restructured `App.tsx`.
-   Live-verified in the browser tool against the real dev server and a
    stub-detector backend with real seeded data (project/source/track):
    ProjectPicker, all 4 sidebar tabs, and the Track Review screen all
    render correctly with no layout regressions; clicked through
    tab-switching, "Review" from an Active Learning queue item
    (confirmed it switches back to Sources tab with the right track
    selected), and a full class-select + Accept submission (confirmed
    the badge updates to ACCEPTED and the success message renders).
-   Not tested: RTSP panel's live/reconnecting/disconnected states
    (would need an actual or faked RTSP source), and Windows
    high-contrast/forced-colors accessibility modes.

## Post-Phase-9 Session: Desktop Build & Packaging

Not one of `docs/02_IMPLEMENTATION_PLAN.md`'s numbered phases - a
direct user request ("how do I make a build of this") to turn the
working dev setup into a real, distributable Windows build. Verified
by actually running the packaged app, not just configuring it.

### What was built

-   **`backend/launcher.py`**: the PyInstaller entry point (separate
    from `app/main.py`, which stays the dev-mode entry point via
    `uvicorn app.main:app`). Points data/workspace/model-weight paths
    at `%LOCALAPPDATA%\ANPR-ATCC-Dataset-Studio\` (never next to the
    executable - an installed app under Program Files is often not
    writable without elevation), creates the DB schema, starts
    uvicorn.
-   **PyInstaller packaging** of the backend (`backend/README.md` has
    the exact command): onedir output, ~1.1GB, bundling torch,
    torchvision, onnxruntime, opencv, ultralytics, RapidOCR, and the
    `trackers`/`supervision` packages this project depends on.
-   **`electron-builder`** configured in `desktop/package.json`
    (`npm run dist`): packages the Electron app plus the PyInstaller
    backend (via `extraResources`) into a Windows build.
-   **`electron/main.ts`** now spawns the bundled backend as a child
    process on launch (waiting for `/health` before loading the UI)
    and stops it on quit - previously it only ever loaded the
    renderer, assuming a backend was already running separately.

### Three real bugs found by actually running the packaged build, not by reading PyInstaller's docs

1.  Assumed PyInstaller onedir puts bundled data files next to the
    `.exe`. Wrong as of PyInstaller 6.x - they're under an `_internal/`
    subfolder. Would have mattered less after fix #2 below removed the
    need for those files entirely, but is the reason fix #2 was found
    in the first place (chasing why `alembic.ini` wasn't where
    expected led to actually running the build).
2.  Alembic's migration path (`command.upgrade`) dynamically
    file-loads `env.py`, which itself dynamically file-loads every
    revision script - this doesn't survive being frozen
    (`ModuleNotFoundError: No module named 'app'` from inside the
    packaged exe, despite `import app` working everywhere else in the
    same process). Fixed by not using Alembic in the packaged build at
    all: `Base.metadata.create_all()` produces an identical end state
    for a fresh install (there is no existing packaged-app database to
    migrate *from* yet), without any of that fragility. This is a
    deliberate, documented scope limit, not an oversight - see
    `launcher.py`'s docstring.
3.  `uvicorn.run("app.main:app", ...)` (the string form) makes uvicorn
    re-import the module by name at runtime - same class of problem,
    confirmed by running it (`Could not import module "app.main"`).
    Fixed by importing the FastAPI `app` object directly and passing
    it to `uvicorn.run()` instead.

After all three fixes, the packaged `.exe` was run directly (not just
built) and verified: health check, project creation, video import, and
a **full detect+track run** (loading `yolo26n.pt` through torch,
running ByteTrack) all completed successfully, with data correctly
landing under `%LOCALAPPDATA%`. RapidOCR specifically wasn't
re-exercised inside the frozen build in this pass (no track existed to
run it against) - worth a specific check before relying on OCR in a
packaged build, though `--collect-all rapidocr` and
`--collect-all onnxruntime` (the same runtime the working YOLO path
also depends on) make a failure here less likely than it would
otherwise be.

### electron-builder's NSIS target: a real environment limitation, not a code bug

Building the actual installer (`npm run dist`, `win.target: "nsis"`)
fails in this sandbox: electron-builder downloads a `winCodeSign`
package (bundling macOS signing tools alongside the Windows ones) and
extracting it requires creating symlinks, which Windows blocks for
non-elevated processes unless Developer Mode is on. `CSC_IDENTITY_AUTO_DISCOVERY=false`
(the standard fix for the code-signing-only case) delayed but didn't
avoid this - NSIS packaging itself hits the same requirement later.

This is a system-level permission question, not something to route
around silently - "modifying system or security settings" is on this
session's explicit prohibited-actions list, so enabling Developer Mode
was never attempted. To get the real NSIS `.exe` installer, do one of:

-   Enable Developer Mode: Settings -> Privacy & security -> For
    developers -> Developer Mode -> On. Then `npm run dist` in
    `desktop/` should complete the NSIS step too.
-   Run `npm run dist` from an elevated (Administrator) terminal.

**What was verified instead, since the config target itself couldn't
be**: `npx electron-builder --win dir` (skips NSIS, produces
`release/win-unpacked/` directly) completed the actual packaging step
successfully every time - the reported failure happens in a later,
non-essential step that runs regardless of target. The resulting
`release/win-unpacked/ANPR-ATCC Dataset Studio.exe` **was launched
directly and confirmed working**: Electron started, spawned
`anpr-atcc-backend.exe` as a child process automatically, and the
backend became healthy within its normal startup window - the full
intended runtime behavior, working end to end, just not yet wrapped in
an NSIS installer.

### Known issues / assumptions (this session)

-   `win.target` in `desktop/package.json` is still `"nsis"` (the
    intended default) - it was not changed to `"dir"` permanently,
    since NSIS is genuinely what should ship once Developer Mode/admin
    is available. Don't "fix" the failing NSIS build by silently
    swapping the target without the user asking for that trade-off.
-   The backend build is ~1.1GB and the full packaged app will be
    larger - no attempt was made to slim it down (e.g. dropping the
    non-headless `opencv-python` that coexists with
    `opencv-python-headless` in the venv, which is itself worth
    investigating separately - see below).
-   Noticed but did not fix: both `opencv-python` and
    `opencv-python-headless` are installed in `backend/.venv`. Only
    `opencv-python-headless` is an intended dependency
    (`pyproject.toml`); the non-headless one arrived transitively
    (likely via the `ultralytics` ecosystem) and its coexistence with
    the headless variant hasn't caused an observed problem, but is
    worth a closer look before shipping - two `cv2` native binary sets
    loaded into the same process is the kind of thing that works until
    it doesn't.
-   No auto-update mechanism, code signing, or release/CI pipeline -
    this covers "can a build be produced and does it run", not a
    shippable release process.
-   Cross-platform (`mac`/`linux` targets in `package.json`) are
    configured but entirely unverified - this session only had a
    Windows sandbox to test in.

## Current Phase

Phase 9 --- RTSP (complete) - **this was the last phase in
`docs/02_IMPLEMENTATION_PLAN.md`**. See "What's left" at the bottom of
this file for what that does and doesn't mean.

## Completed This Session

### A real, documented testing gap - read this first

RTSP needed genuine live-stream testing to mean anything, so before
writing any adapter code this session tried hard to stand up a real
local RTSP test server: confirmed `ffmpeg` is installed, generated a
test video, and attempted `ffmpeg -rtsp_flags listen` as a local RTSP
server. It never actually bound the listen port (`Get-NetTCPConnection`
showed nothing listening despite the process running) - both a
`ffprobe` client and an `cv2.VideoCapture` client timed out (the
OpenCV one after the standard 30s). A direct raw-socket test confirmed
this sandbox's basic TCP loopward networking works fine (a plain
`socket.listen()`/`connect()` round-trip succeeded immediately), which
rules out a blanket network restriction and points at this specific
`ffmpeg` build/environment's RTSP listen-mode muxer. Installing a
third-party RTSP server binary (e.g. mediamtx) to work around this was
not attempted - "downloading and executing files from untrusted
sources" is a hard rule this session follows regardless of how
convenient it would have been here.

Given that, every layer that talks to the actual network/RTSP
protocol (`OpenCvRtspConnection`, which just wraps `cv2.VideoCapture`
- the same call already proven correct for offline files and RTSP
URLs in general) is real, standard code but **could not be verified
against a live stream in this environment**. Everything above that
layer - reconnect state machine, bounded buffer, capture/processing
threading, and the full detect -> track -> rank -> persist pipeline -
**was** verified for real, using a fake `RtspConnection` that's
injected exactly where a real network client would be (dependency-
injected at both the unit-test level and the FastAPI level). This is
not a mock of "the RTSP adapter" - it's a fake of only the one
component (the socket/FFmpeg call) that couldn't be exercised here,
with every other real line of code actually running.

### Backend

-   **RTSP source adapter** (`app/services/rtsp_source.py`):
    `RtspConnection` is a 3-method interface (`open`/`read`/
    `release`); `OpenCvRtspConnection` is the real implementation
    (same `cv2.VideoCapture` machinery as offline files - RTSP URLs
    are just another FFmpeg-demuxable source string).
    `RtspSourceAdapter` owns the reconnect state machine and is
    fully unit-tested via a scripted fake connection (10 tests:
    connect success/failure, read success/failure, backoff timing
    and capping, exhaustion, and - found and fixed mid-session - a
    stop-request now interrupts an in-progress backoff instead of
    blocking through the rest of it).
-   **Bounded rolling buffer** (`app/services/rolling_buffer.py`):
    a `deque(maxlen=...)`-backed FIFO. Live streams are unbounded, so
    unlike offline processing there's no known frame count to size
    anything around - eviction past `maxlen` is the deliberate
    backpressure valve, and `dropped_count` makes "processing can't
    keep up with capture" directly observable rather than silent.
-   **Reconnect behavior**: exponential backoff (configurable
    initial/max/multiplier), capped attempts, and - the fix made this
    session after a test caught it - a live `on_attempt` callback so a
    caller polling status from another thread sees `reconnect_attempts`
    update *during* a long reconnect sequence, not only after it
    finishes.
-   **Same downstream track/review contracts**: `track_processor.py`
    was refactored (behavior-preserving - the full existing test suite
    passed unchanged before any new tests were added) to expose
    `observe_frame` (per-frame detect -> track -> accumulate) and
    `persist_observations` (rank -> bucket -> write) as reusable
    functions. `rtsp_session.py` calls the exact same two functions
    offline processing calls - there is no separate "RTSP version" of
    detection, tracking, quality ranking, or persistence logic to
    drift out of sync with the offline path.
-   **Live capture session** (`app/services/rtsp_session.py`): two
    background daemon threads per session - a capture thread that
    only ever reads frames and manages reconnects, and a processing
    thread that drains the buffer and runs the pipeline. A local,
    single-user desktop app doesn't need a real distributed task queue
    for this. New endpoints: `POST /projects/{id}/sources/rtsp/start`,
    `GET /processing-runs/{id}/rtsp/status`, `POST /processing-runs/{id}/rtsp/stop`.
    Sessions live in an in-memory registry
    (`rtsp_session_registry.py`) - see known issues.
-   Live RTSP sources get sentinel `frame_count=0`/`duration_ms=0` on
    their `Source` row (a live stream has neither, unlike a file) and
    `fps` is the caller's *expected* value, not a probed one - many
    real cameras don't report a reliable FPS anyway.
-   **No schema migration this phase** - `sources.type="rtsp"` fits
    the existing columns; sessions are process-local, not DB rows.

### Desktop

-   New `RtspPanel`: URL + expected-FPS inputs, "Start Live Capture",
    a 1s status poll (connected/reconnecting/disconnected, frame/
    drop/track counts), and "Stop". Verified live end-to-end with a
    fake camera connection wired through the same dependency-override
    mechanism as the backend tests - the resulting track opened in
    the exact same `TrackReview` UI as an offline-processed track
    (filmstrip, class editor, bbox editor, OCR panel, Accept) and was
    reviewed successfully. This is the most concrete evidence
    available in this environment that "same downstream track/review
    contracts" actually holds.

## Files Changed

-   Backend: `app/services/{rolling_buffer,rtsp_source,rtsp_session,rtsp_session_registry}.py`,
    `app/schemas/rtsp.py`, `app/api/rtsp.py`; refactored
    `app/services/track_processor.py` (extracted `observe_frame`/
    `persist_observations`, no behavior change). Tests:
    `services/test_rolling_buffer.py`, `services/test_rtsp_source.py`,
    `services/test_rtsp_session.py`, `test_rtsp_api.py`.
-   Desktop: `src/components/RtspPanel.tsx`; extended `src/types.ts`,
    `src/api.ts`, `src/App.tsx`, `src/index.css`.

## Tests / Commands Run

-   `pytest`: 157 passed (27 new). The refactor of `track_processor.py`
    was verified behavior-preserving by running the full suite
    immediately after it, before writing a single new test.
-   Desktop `vitest`/`tsc -b`: unchanged 1 test, clean typecheck.
-   **Full live UI walkthrough** using a fake camera connection
    (backend-level dependency override, not a UI mock): started a
    live capture session, watched status go from "connected" through
    839 captured frames with 0 drops, clicked Stop, watched it
    transition to "disconnected" / "Session ended.", and found exactly
    one persisted track in the normal Track Browser. Opened it -
    identical `TrackReview` UI to an offline track - picked "Car/Jeep/Van",
    clicked Accept, and the sidebar badge updated to "accepted". All
    test artifacts and processes cleaned up afterward.
-   Two real bugs were found and fixed by these tests, not assumed
    away: (1) `RtspSourceAdapter.reconnect()` originally blocked
    through its *entire* backoff sequence even after `stop()` was
    called - fixed with the `should_stop` check now covered by its
    own test. (2) `RtspCaptureSession`'s status only synced
    `reconnect_attempts` from inside the main capture loop, so a
    long-running *initial* connection attempt was invisible to anyone
    polling status - fixed with the `on_attempt` callback, and this is
    exactly the kind of bug a live UI walkthrough (not just unit
    tests with instant fakes) is likely to surface.

## Known Issues / Assumptions

-   **The RTSP protocol/socket layer itself was never exercised
    against a real stream in this sandbox** - see the detailed note
    above. Recommend a smoke test against a real camera or a working
    local RTSP server (e.g. on a machine where `ffmpeg -rtsp_flags
    listen` or a proper server actually binds) before relying on this
    in production.
-   Live sessions live only in an in-memory registry - they do not
    survive a backend restart, and there's no persisted "session was
    running, resume it" recovery. Reasonable for a local desktop tool
    session, but worth knowing if the backend process is expected to
    restart while a camera is live.
-   No automatic cleanup of finished sessions from the in-memory
    registry - long-running server processes with many started/stopped
    sessions will accumulate small `RtspCaptureSession` objects in
    memory. Not a concern at this project's current scale.
-   Live-capture timestamps are wall-clock-relative to session start
    (`time.monotonic()`-based), not derived from any native frame rate
    - a live stream has no fixed FPS the way a file does, so
    `frame_sampler.py`'s frame-index-based determinism intentionally
    does not apply here (this was flagged as an open question at the
    end of Phase 8's handoff, and this is the actual resolution: two
    different, each-correct-for-its-source timestamp strategies, not
    one contract stretched to cover both).
-   No file-picker equivalent for entering an RTSP URL (types a
    string, same as the offline video-path input) - consistent with
    the rest of this app's minimal-UI approach so far, not a gap
    specific to this phase.

## Data-Preservation Checks

-   The live capture pipeline calls the exact same `persist_observations`
    function offline processing does - every activated tracked
    detection becomes a `frame_candidates` row with a saved crop, the
    same as offline, and nothing about "how a track was captured"
    changes what happens to it once persisted (same bucket/review_status
    workflow, same export eligibility, same everything downstream).
-   Stopping a session never discards already-persisted tracks from an
    earlier periodic persist (`persist_interval_seconds`) - only the
    not-yet-persisted tail of observations gets written at stop time;
    nothing gets rolled back or dropped.

## What's left

This closes out every phase in `docs/02_IMPLEMENTATION_PLAN.md`
(0 through 9). That does not mean the product is "done" - it means
every phase the plan defined has a working implementation with real
tests and (where this sandbox allowed) live verification. Genuine
open items, everything already flagged honestly along the way rather
than glossed over:

-   No real gantry footage has ever been run through this pipeline
    (flagged since Phase 4) - `Downloads/atcc1.mp4` etc. are still
    sitting unused, unprompted.
-   Every quality/confidence/ranking/reconnect threshold in this
    codebase is an uncalibrated default, waiting on that same real
    footage.
-   `yolo26n.pt` is still a generic COCO-pretrained checkpoint, not a
    model trained on the 20 ATCC classes - Phase 8's retraining
    handoff exists and works, but nobody has run it yet.
-   Background-job execution for long-running requests
    (`task_fe173a9f`) is still open.
-   The RTSP protocol layer's live-stream verification gap above.

## Do Not Redo

-   Do not redesign the product requirements unless a real
    implementation constraint requires a documented decision.
-   Do not replace the chosen stack without explicit approval. Three
    ML component choices were direct user decisions in response to a
    real constraint, not defaults - don't silently swap any of them:
    `yolo26n.pt`, the `trackers` package's ByteTrack (not
    `supervision.ByteTrack`, deprecated), RapidOCR (not PaddleOCR -
    `paddlepaddle` isn't installable here).
-   Do not re-scaffold `backend/` or `desktop/` from scratch; extend
    what exists.
-   Do not re-derive offline frame timestamps from wall-clock/decode
    timing - `frame_sampler.py`'s frame-index-based approach is what
    makes offline sampling reproducible. RTSP's wall-clock-relative
    timestamps are a deliberate, separate strategy for a source that
    has no fixed frame count - not a violation of this rule, don't
    try to unify them.
-   Do not call `observe_frame`/`persist_observations` from a new,
    parallel implementation for any future source type - route it
    through these shared functions the way both offline and RTSP do,
    or the "same downstream contracts" guarantee silently breaks.
-   Do not download or execute a third-party binary (e.g. an RTSP
    server) to work around this sandbox's testing gap without the
    user's explicit go-ahead - this was a deliberate line this session
    didn't cross even though it would have made testing easier.
-   Do not use personal files outside the repo (e.g. anything under
    `Downloads/`) for testing without asking first.
