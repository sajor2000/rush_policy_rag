#!/usr/bin/env python3
"""Probe FastAPI endpoints to capture latency baselines before/after architecture changes."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

import requests


DEFAULT_BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")


@dataclass
class EndpointResult:
    name: str
    method: str
    url: str
    iterations: int
    latency_ms: List[float]
    status_codes: List[Optional[int]]
    errors: List[str]

    @property
    def successes(self) -> int:
        return sum(1 for code in self.status_codes if code and 200 <= code < 400)

    @property
    def failures(self) -> int:
        return self.iterations - self.successes

    def _percentile(self, percentile: float) -> Optional[float]:
        successful_durations = [
            latency for latency, code in zip(self.latency_ms, self.status_codes)
            if code and 200 <= code < 400
        ]
        if not successful_durations:
            return None

        successful_durations.sort()
        k = (len(successful_durations) - 1) * percentile
        f = int(k)
        c = min(f + 1, len(successful_durations) - 1)
        if f == c:
            return successful_durations[int(k)]
        d0 = successful_durations[f] * (c - k)
        d1 = successful_durations[c] * (k - f)
        return d0 + d1

    def summary(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "method": self.method,
            "url": self.url,
            "iterations": self.iterations,
            "successes": self.successes,
            "failures": self.failures,
            "avg_ms": statistics.fmean(self.latency_ms) if self.latency_ms else None,
            "p50_ms": self._percentile(0.5),
            "p95_ms": self._percentile(0.95),
            "status_codes": self.status_codes,
            "errors": self.errors,
        }


def measure_endpoint(
    session: requests.Session,
    name: str,
    method: str,
    url: str,
    iterations: int,
    timeout: float,
    payload: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
) -> EndpointResult:
    latencies: List[float] = []
    codes: List[Optional[int]] = []
    errors: List[str] = []

    for _ in range(iterations):
        start = time.perf_counter()
        try:
            if method == "GET":
                response = session.get(url, timeout=timeout, headers=headers)
            else:
                response = session.post(url, json=payload, timeout=timeout, headers=headers)
            codes.append(response.status_code)
        except Exception as exc:  # pylint: disable=broad-except
            codes.append(None)
            errors.append(str(exc))
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            latencies.append(elapsed_ms)

    return EndpointResult(
        name=name,
        method=method,
        url=url,
        iterations=iterations,
        latency_ms=latencies,
        status_codes=codes,
        errors=errors,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Measure FastAPI endpoint latency baselines")
    parser.add_argument("--backend-url", default=DEFAULT_BACKEND_URL, help="Base URL for the FastAPI backend")
    parser.add_argument("--iterations", type=int, default=5, help="Number of requests per endpoint")
    parser.add_argument("--timeout", type=float, default=30.0, help="Request timeout in seconds")
    parser.add_argument("--include-search", action="store_true", help="Include POST /api/search measurements")
    parser.add_argument("--include-chat", action="store_true", help="Include POST /api/chat measurements")
    parser.add_argument("--chat-message", default="List HIPAA policies", help="Message to send when testing chat")
    parser.add_argument("--search-query", default="hand hygiene", help="Query to send when testing search")
    parser.add_argument("--admin-key", default=os.environ.get("ADMIN_API_KEY"), help="Optional X-Admin-Key header value")
    parser.add_argument("--output", help="Optional JSON file to store raw results")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    base_url = args.backend_url.rstrip("/")
    headers = {"Accept": "application/json"}
    if args.admin_key:
        headers["X-Admin-Key"] = args.admin_key

    session = requests.Session()
    results: List[EndpointResult] = []

    results.append(
        measure_endpoint(
            session=session,
            name="health",
            method="GET",
            url=f"{base_url}/health",
            iterations=args.iterations,
            timeout=args.timeout,
            headers=headers,
        )
    )

    if args.include_search:
        results.append(
            measure_endpoint(
                session=session,
                name="search",
                method="POST",
                url=f"{base_url}/api/search",
                iterations=args.iterations,
                timeout=args.timeout,
                payload={"query": args.search_query, "top": 3},
                headers=headers,
            )
        )

    if args.include_chat:
        results.append(
            measure_endpoint(
                session=session,
                name="chat",
                method="POST",
                url=f"{base_url}/api/chat",
                iterations=args.iterations,
                timeout=args.timeout,
                payload={"message": args.chat_message},
                headers=headers,
            )
        )

    print("\nLatency Summary (ms)")
    print("=" * 60)
    for result in results:
        summary = result.summary()
        print(f"Endpoint : {summary['name']} ({summary['method']} {result.url})")
        print(f"  Successes : {summary['successes']} / {summary['iterations']}")
        print(f"  Avg       : {summary['avg_ms']:.2f} ms" if summary['avg_ms'] is not None else "  Avg       : n/a")
        print(f"  P50       : {summary['p50_ms']:.2f} ms" if summary['p50_ms'] is not None else "  P50       : n/a")
        print(f"  P95       : {summary['p95_ms']:.2f} ms" if summary['p95_ms'] is not None else "  P95       : n/a")
        if summary['errors']:
            print(f"  Errors    : {len(summary['errors'])} (see JSON output or rerun with --iterations=1 for details)")
        print()

    if args.output:
        output_payload = {
            "backend_url": base_url,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "iterations": args.iterations,
            "results": [result.summary() | {"raw": asdict(result)} for result in results],
        }
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(output_payload, f, indent=2)
        print(f"Saved detailed results to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
