#!/usr/bin/env python3
"""
Benchmark script for the PaddleOCR microservice.

Usage:
  python services/paddleocr/scripts/benchmark_local_ocr.py \
      --images-dir ./artifacts/vision-samples --mode http \
      --endpoint http://localhost:8008/v1/ocr

  # To run inference via in-process service (without HTTP):
  python services/paddleocr/scripts/benchmark_local_ocr.py \
      --images-dir ./artifacts/vision-samples --mode local
"""

from __future__ import annotations

import argparse
import base64
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Iterable, List, Tuple

import structlog

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.paddleocr.app import get_default_service  # noqa: E402

logger = structlog.get_logger(__name__)

SUPPORTED_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tiff")


def iter_images(path: Path, limit: int | None = None) -> Iterable[Path]:
    count = 0
    for file in sorted(path.rglob("*")):
        if not file.is_file():
            continue
        if file.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        yield file
        count += 1
        if limit and count >= limit:
            break


def run_local(files: List[Path]) -> Tuple[List[float], List[int]]:
    service = get_default_service()
    durations: List[float] = []
    lines: List[int] = []
    for file in files:
        image_bytes = file.read_bytes()
        start = time.time()
        result = service.run_ocr(image_bytes)
        duration = time.time() - start
        durations.append(duration)
        lines.append(result["aggregates"]["line_count"])
        logger.info(
            "OCR local",
            file=str(file),
            duration_ms=int(duration * 1000),
            lines=result["aggregates"]["line_count"],
            confidence_mean=round(result["aggregates"]["confidence_mean"], 4),
        )
    return durations, lines


def run_http(files: List[Path], endpoint: str, timeout: float) -> Tuple[List[float], List[int]]:
    import httpx

    client = httpx.Client(timeout=timeout)
    durations: List[float] = []
    lines: List[int] = []
    for file in files:
        image_bytes = file.read_bytes()
        payload = {
            "image_base64": base64.b64encode(image_bytes).decode(),
            "return_image": False,
        }
        start = time.time()
        response = client.post(endpoint, json=payload)
        duration = time.time() - start
        response.raise_for_status()
        data = response.json()
        durations.append(duration)
        lines.append(data["aggregates"]["line_count"])
        logger.info(
            "OCR http",
            file=str(file),
            duration_ms=int(duration * 1000),
            lines=data["aggregates"]["line_count"],
            confidence_mean=round(data["aggregates"]["confidence_mean"], 4),
        )
    return durations, lines


def summarize(durations: List[float], lines: List[int]) -> dict:
    if not durations:
        return {
            "count": 0,
            "duration_mean_ms": 0,
            "duration_median_ms": 0,
            "duration_p95_ms": 0,
            "duration_min_ms": 0,
            "duration_max_ms": 0,
            "throughput_img_per_s": 0,
            "lines_mean": 0,
        }
    summary = {
        "count": len(durations),
        "duration_mean_ms": round(statistics.mean(durations) * 1000, 2),
        "duration_median_ms": round(statistics.median(durations) * 1000, 2),
        "duration_min_ms": round(min(durations) * 1000, 2),
        "duration_max_ms": round(max(durations) * 1000, 2),
        "throughput_img_per_s": round(len(durations) / sum(durations), 3),
        "lines_mean": round(statistics.mean(lines), 2) if lines else 0,
    }
    if len(durations) > 1:
        summary["duration_p95_ms"] = round(statistics.quantiles(durations, n=100)[94] * 1000, 2)
    else:
        summary["duration_p95_ms"] = summary["duration_max_ms"]
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark PaddleOCR service")
    parser.add_argument("--images-dir", type=Path, required=True, help="Directory with sample images")
    parser.add_argument("--max-files", type=int, default=None, help="Limit number of files")
    parser.add_argument(
        "--mode",
        choices=("local", "http"),
        default="http",
        help="local — использовать PaddleOCR напрямую, http — сервис REST",
    )
    parser.add_argument(
        "--endpoint",
        type=str,
        default="http://localhost:8008/v1/ocr",
        help="Endpoint OCR сервиса (используется в http-режиме)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP timeout в секундах",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Вывести финальный результат в JSON",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.images_dir.exists():
        raise SystemExit(f"Directory not found: {args.images_dir}")

    files = list(iter_images(args.images_dir, args.max_files))
    if not files:
        raise SystemExit("No image files found for benchmarking.")

    logger.info("Starting OCR benchmark", mode=args.mode, file_count=len(files))

    if args.mode == "local":
        durations, lines = run_local(files)
    else:
        durations, lines = run_http(files, endpoint=args.endpoint, timeout=args.timeout)

    summary = summarize(durations, lines)
    summary["mode"] = args.mode
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print("\n=== OCR Benchmark Summary ===")
        for key, value in summary.items():
            print(f"{key:25}: {value}")


if __name__ == "__main__":
    main()

