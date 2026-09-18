"""Time the detector over a video, on a chosen device.

Exists so "the GPU is faster" is a number somebody can reproduce rather
than a claim in a commit message. Run it once per device and compare:

    python scripts/benchmark_detection.py <video> --device cpu
    python scripts/benchmark_detection.py <video> --device cuda:0

Warm-up frames are excluded from the timing: the first CUDA inference
pays for context creation and kernel autotuning, which would otherwise
dominate a short run and understate the GPU.
"""

import argparse
import statistics
import time
from pathlib import Path

import cv2

from app.core.config import get_settings
from app.ml.yolo_detector import DEFAULT_MODEL_WEIGHTS, YoloDetector

DEFAULT_FRAMES = 200
DEFAULT_WARMUP = 10


def benchmark(video: Path, device: str, frames: int, warmup: int) -> dict:
    weights = get_settings().resolved_model_weights_dir() / DEFAULT_MODEL_WEIGHTS
    detector = YoloDetector(weights=str(weights), device=device)

    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise SystemExit(f"Could not open video: {video}")

    durations: list[float] = []
    detections = 0
    try:
        for index in range(warmup + frames):
            ok, frame = capture.read()
            if not ok:
                break
            started = time.perf_counter()
            found = detector.detect(frame)
            elapsed = time.perf_counter() - started
            if index >= warmup:
                durations.append(elapsed)
                detections += len(found)
    finally:
        capture.release()

    if not durations:
        raise SystemExit(f"No frames decoded past the {warmup}-frame warm-up: {video}")

    total = sum(durations)
    return {
        "device": detector.device,
        "frames": len(durations),
        "total_seconds": total,
        "fps": len(durations) / total,
        "mean_ms": statistics.mean(durations) * 1000,
        "median_ms": statistics.median(durations) * 1000,
        "detections": detections,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("video", type=Path)
    parser.add_argument("--device", default=None, help="auto, cpu, cuda or cuda:<index>. Defaults to the configured device.")
    parser.add_argument("--frames", type=int, default=DEFAULT_FRAMES)
    parser.add_argument("--warmup", type=int, default=DEFAULT_WARMUP)
    args = parser.parse_args()

    result = benchmark(args.video, args.device, args.frames, args.warmup)
    print(f"device       {result['device']}")
    print(f"frames       {result['frames']} (after {args.warmup} warm-up)")
    print(f"total        {result['total_seconds']:.2f} s")
    print(f"throughput   {result['fps']:.2f} fps")
    print(f"per frame    {result['mean_ms']:.1f} ms mean / {result['median_ms']:.1f} ms median")
    print(f"detections   {result['detections']}")


if __name__ == "__main__":
    main()
