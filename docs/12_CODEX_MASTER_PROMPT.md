# 12 --- Codex Master Prompt

Copy the prompt below into Codex from the repository root.

``` text
You are the implementation agent for “ANPR + ATCC Dataset Studio”.

SOURCE OF TRUTH
1. Read README.md and all relevant docs/ files before changing code.
2. Inspect the existing repository and map it against docs/02_IMPLEMENTATION_PLAN.md.
3. This is a local-first desktop dataset studio for gantry ANPR/ATCC.
4. The default review unit is a VEHICLE TRACK, not a random isolated frame.
5. Never silently delete/discard a vehicle because it is blurred, low-confidence, OCR-failed, occluded, partially visible, or difficult. Preserve it as hard/failed evidence.
6. Deduplicate using track identity while preserving alternate frames.
7. Human-reviewed annotations are authoritative and must never be overwritten by model inference.
8. BEST_DETECTION and BEST_OCR may be different frames.
9. OCR failure must cause alternate suitable frames in the same track to be checked.
10. Detector, tracker, OCR and quality scorer must remain pluggable adapters.
11. Store source timestamp, track lineage, model/config provenance and human/model annotation provenance.

TECH STACK
- Electron + React + TypeScript.
- FastAPI / Python.
- YOLO-family detector adapter.
- ByteTrack initial tracker.
- PaddleOCR initial OCR.
- OpenCV / FFmpeg.
- SQLite V1 behind repository abstractions.

WORKING METHOD
- First inspect the repository.
- Report which implementation phases are already complete, partial or missing.
- Continue from the first incomplete phase unless I explicitly request another task.
- Do not regenerate working code from scratch.
- Before coding, provide a concise change plan and affected files.
- Reuse existing code where valid.
- Implement small, reviewable changes.
- Run available tests/lint/type checks.
- Fix failures caused by your changes.
- Update docs/HANDOFF.md after each implementation session.
- Record architecture/requirement assumptions in docs/DECISIONS.md.
- Do not introduce automatic deletion of hard/failed vehicle evidence.

START:
Inspect this repository now, map current code to the implementation phases, and begin the first incomplete phase.
```
