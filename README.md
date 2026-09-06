# ANPR + ATCC Dataset Studio

Local-first AI-assisted dataset studio for gantry ANPR and ATCC work.

## Core Principle

**Never silently lose a vehicle.** Blur, low confidence, OCR failure,
occlusion, poor lighting, or partial visibility must not cause automatic
deletion. Difficult samples are retained as `HARD` or
`FAILED/NEEDS_REVIEW`.

## Product Stack

-   Desktop: Electron + React + TypeScript
-   Backend: FastAPI / Python
-   Detection: YOLO-family adapter
-   Tracking: ByteTrack initially
-   OCR: PaddleOCR
-   Media: OpenCV / FFmpeg
-   Metadata DB: SQLite for V1
-   Future DB option: PostgreSQL

## Documentation

Read in this order: 1. `docs/01_PRD.md` 2.
`docs/02_IMPLEMENTATION_PLAN.md` 3. `docs/03_TRD.md` 4.
`docs/04_SYSTEM_ARCHITECTURE.md` 5. `docs/05_DATABASE_DESIGN.md` 6.
`docs/06_UI_UX_SPEC.md` 7. `docs/07_ML_CV_PIPELINE.md` 8.
`docs/08_TESTING_ACCEPTANCE.md` 9. `docs/09_DEVELOPMENT_GUIDELINES.md`
10. `docs/10_DEPLOYMENT_GUIDE.md` 11. `docs/11_ROADMAP.md` 12.
`docs/12_CODEX_MASTER_PROMPT.md` 13. `docs/DECISIONS.md` 14.
`docs/HANDOFF.md`

## Implementation Start

Codex must inspect the repository first and continue from the first
incomplete phase in `02_IMPLEMENTATION_PLAN.md`. Do not regenerate
working code from scratch.

## Getting Started (Phase 0)

-   `backend/` — FastAPI backend. See `backend/README.md`.
-   `desktop/` — Electron + React + TypeScript desktop shell. See
    `desktop/README.md`.

Current status: `docs/HANDOFF.md`.
