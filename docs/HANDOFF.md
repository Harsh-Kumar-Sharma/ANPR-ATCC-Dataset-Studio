# Development Handoff

This file is updated by Codex after every meaningful implementation
session.

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
