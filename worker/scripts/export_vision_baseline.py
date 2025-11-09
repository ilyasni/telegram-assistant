#!/usr/bin/env python3
"""
Context7: Экспорт baseline метрик Vision из Prometheus.

Сохраняет значения ключевых метрик (vision_tokens_used_total,
vision_analysis_requests_total, vision_media_total) в JSON/STDOUT.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List

import httpx
import structlog


logger = structlog.get_logger(__name__)


DEFAULT_PROM_URL = os.getenv("PROMETHEUS_BASE_URL", "http://localhost:9090")
METRICS = [
    "vision_tokens_used_total",
    "vision_analysis_requests_total",
    "vision_media_total",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export baseline values for Vision metrics from Prometheus"
    )
    parser.add_argument(
        "--prometheus-url",
        type=str,
        default=DEFAULT_PROM_URL,
        help="Prometheus base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Path to write JSON report (stdout if omitted)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("PROMETHEUS_TIMEOUT", "10")),
        help="HTTP timeout in seconds (default: %(default)s)",
    )
    return parser.parse_args()


async def query_prometheus(
    client: httpx.AsyncClient, base_url: str, metric: str
) -> Dict[str, Any]:
    query = f"sum({metric})"
    url = f"{base_url.rstrip('/')}/api/v1/query"
    params = {"query": query}
    response = await client.get(url, params=params)
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "success":
        raise RuntimeError(f"Prometheus returned status={payload.get('status')}")
    result = payload.get("data", {}).get("result", [])
    value = None
    if result:
        try:
            value = float(result[0]["value"][1])
        except (KeyError, ValueError, IndexError):
            value = None
    return {
        "metric": metric,
        "query": query,
        "value": value,
        "raw": result,
    }


async def collect_metrics(base_url: str, timeout: float) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout) as client:
        records: List[Dict[str, Any]] = []
        for metric in METRICS:
            try:
                record = await query_prometheus(client, base_url, metric)
                logger.info(
                    "Metric collected",
                    metric=metric,
                    value=record.get("value"),
                    base_url=base_url,
                )
                records.append(record)
            except Exception as exc:
                logger.error(
                    "Failed to collect metric",
                    metric=metric,
                    base_url=base_url,
                    error=str(exc),
                )
                records.append(
                    {
                        "metric": metric,
                        "query": f"sum({metric})",
                        "value": None,
                        "error": str(exc),
                    }
                )
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "prometheus_url": base_url,
        "metrics": records,
    }


def write_output(report: Dict[str, Any], target: Path | None) -> None:
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if target:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload + "\n", encoding="utf-8")
        logger.info("Baseline metrics written", path=str(target))
    else:
        sys.stdout.write(payload + "\n")


def main() -> None:
    args = parse_args()
    report = asyncio.run(collect_metrics(args.prometheus_url, args.timeout))
    if report is None:
        raise SystemExit("Failed to gather metrics")
    write_output(report, args.output)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(130)

