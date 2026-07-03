#!/usr/bin/env python3
"""Run the KylinGuard OS-agent security benchmark."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from kylinguard.security_benchmark import (  # noqa: E402
    evaluate_benchmark,
    load_benchmark,
)


DEFAULT_BENCHMARK = REPO_ROOT / "benchmarks" / "os_agent_security_benchmark.json"


def _format_rate(value: float) -> str:
    return f"{value * 100:.1f}%"


def print_summary(report: dict) -> None:
    print(f"{report['name']} ({report.get('version')})")
    print(f"Total: {report['total_passed']}/{report['total_cases']} cases passed")
    print()

    for suite, stats in report["suites"].items():
        line = (
            f"- {suite}: {stats['cases_passed']}/{stats['cases_total']} "
            f"cases, {stats['checks_passed']}/{stats['checks_total']} checks, "
            f"pass_rate={_format_rate(stats['case_pass_rate'])}"
        )
        if suite == "safe_operation":
            line += (
                f", false_block_rate="
                f"{_format_rate(stats['false_block_rate'])}"
            )
        print(line)

    if report["failures"]:
        print()
        print("Failures:")
        for failure in report["failures"]:
            print(f"- {failure['id']} [{failure['suite']}/{failure['category']}]")
            for check in failure["failed_checks"]:
                print(
                    f"  {check['type']}: expected={check['expect']} "
                    f"actual={check['actual']} reason={check['reason']}"
                )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run KylinGuard's 60-case OS-agent security benchmark.")
    parser.add_argument(
        "--benchmark",
        type=Path,
        default=DEFAULT_BENCHMARK,
        help="Path to benchmark JSON dataset.")
    parser.add_argument(
        "--json-out",
        type=Path,
        help="Optional path for a machine-readable JSON report.")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print the human summary when failures occur.")
    args = parser.parse_args(argv)

    data = load_benchmark(args.benchmark)
    report = evaluate_benchmark(data)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8")

    if not args.quiet or not report["passed"]:
        print_summary(report)

    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
