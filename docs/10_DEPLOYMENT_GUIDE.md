# 10 --- Local Deployment Guide

## V1 Target

Windows local workstation. Backend and desktop app run locally. GPU
inference uses CUDA when available.

## Prerequisites

-   Git.
-   Python 3.11 or 3.12 recommended for ML package compatibility.
-   Node.js LTS.
-   FFmpeg.
-   NVIDIA driver and compatible PyTorch build for GPU usage.

## Repository Data Policy

Do not commit: - source videos; - datasets; - model weights; - generated
crops; - SQLite runtime DB; - secrets.

Use `.gitignore` and `.env`.

## Runtime

Electron desktop connects to the local FastAPI backend. Long-running
video/ML work runs through background job components so the UI/API stays
responsive.

## Configuration

### Device

`cpu` / `cuda`, optional GPU index.

### Detector

model path, image size, confidence/NMS thresholds.

### Tracker

association thresholds, lost-track buffer.

### Sampling

processing FPS/adaptive policy.

### Quality

quality thresholds/weights.

### OCR

model/language/device/confidence.

### Storage

workspace/cache paths and limits.

## Calibration

Do not assume a universal FPS. Use a representative 30-second to
2-minute gantry clip and compare multiple processing rates for: - missed
vehicles; - fragmented tracks; - duplicate tracks; - availability of
sharp/OCR-capable frames.

Save the chosen settings as a named processing profile.
