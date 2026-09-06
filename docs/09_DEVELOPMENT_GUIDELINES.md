# 09 --- Development Guidelines

## General

-   Inspect existing code before adding new code.
-   Reuse working architecture.
-   Avoid duplicate parallel implementations.
-   Keep modules small and testable.
-   Use typed request/response/domain models.
-   Centralize configuration.
-   Add migrations for schema changes.

## ML Rules

-   Detector/tracker/OCR behind interfaces.
-   Store model version and configuration per run.
-   Never overwrite human truth with inference.
-   Never automatically delete difficult vehicle tracks.
-   Keep source timestamps and track lineage.

## Error Handling

-   Errors must be actionable.
-   Background job failure must update job state.
-   Avoid silent `except`.
-   Log IDs/context, but not credentials.

## Definition of Done

A feature is done only when: 1. implementation is integrated; 2. happy
and failure paths are tested; 3. errors/logging are usable; 4.
docs/config are updated; 5. data-preservation invariants still pass.

## Codex Rule

Before each phase, Codex should state a concise plan and affected files,
implement, run available tests/lint/type checks, fix regressions caused
by its changes, and update `HANDOFF.md`.
