#!/usr/bin/env python3
"""Snapshot Grafana vision dashboard for baseline comparison."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import structlog


logger = structlog.get_logger(__name__)

DEFAULT_SOURCE = Path("grafana/dashboards/vision_s3_dashboard.json")
DEFAULT_TARGET_DIR = Path("reports/grafana-baseline")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Copy Grafana dashboard JSON to baseline directory")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="Path to source dashboard JSON")
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=DEFAULT_TARGET_DIR,
        help="Directory to store timestamped baseline copies",
    )
    parser.add_argument(
        "--label",
        type=str,
        default=None,
        help="Optional extra suffix (e.g., pre-waveA)",
    )
    return parser.parse_args()


def build_filename(label: str | None) -> str:
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = f"_{label}" if label else ""
    return f"vision_s3_dashboard_{timestamp}{suffix}.json"


def main() -> None:
    args = parse_args()
    if not args.source.exists():
        raise SystemExit(f"Source dashboard not found: {args.source}")

    try:
        data = json.loads(args.source.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"Failed to read dashboard JSON: {exc}") from exc

    args.target_dir.mkdir(parents=True, exist_ok=True)
    target_file = args.target_dir / build_filename(args.label)
    target_file.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info("Grafana baseline saved", source=str(args.source), target=str(target_file))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(130)

