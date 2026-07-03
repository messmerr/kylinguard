"""Security benchmark runner for KylinGuard guardrails."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kylinguard.intent import screen_user_intent
from kylinguard.models import RuleDecision, RuleVerdict
from kylinguard.rules import check_command


EXPECTED_SUITES = {
    "unsafe_block": 21,
    "safe_operation": 24,
    "critical_config_reliability": 15,
}


@dataclass(frozen=True)
class CheckResult:
    case_id: str
    suite: str
    check_type: str
    input: str
    expect: str
    actual: str
    passed: bool
    reason: str
    matched_rule: str | None


@dataclass(frozen=True)
class CaseResult:
    id: str
    suite: str
    category: str
    expected: str
    passed: bool
    checks: list[CheckResult]


def load_benchmark(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_benchmark(data: dict[str, Any]) -> None:
    cases = data.get("cases", [])
    if not isinstance(cases, list):
        raise ValueError("benchmark field 'cases' must be a list")

    ids = [case.get("id") for case in cases]
    duplicates = sorted(case_id for case_id, count in Counter(ids).items()
                        if count > 1)
    if duplicates:
        raise ValueError(f"duplicate benchmark case ids: {duplicates}")

    actual_counts = Counter(case.get("suite") for case in cases)
    declared_counts = data.get("suite_counts") or EXPECTED_SUITES
    if dict(actual_counts) != dict(declared_counts):
        raise ValueError(
            f"suite count mismatch: actual={dict(actual_counts)} "
            f"declared={dict(declared_counts)}")
    if dict(actual_counts) != EXPECTED_SUITES:
        raise ValueError(
            f"benchmark must contain exactly {EXPECTED_SUITES}, "
            f"got {dict(actual_counts)}")

    for case in cases:
        checks = case.get("checks", [])
        if not checks:
            raise ValueError(f"case {case.get('id')} has no checks")
        for check in checks:
            if check.get("type") not in {"intent", "command"}:
                raise ValueError(
                    f"case {case.get('id')} has unsupported check type "
                    f"{check.get('type')!r}")
            if check.get("expect") not in {"deny", "allow", "review", "not_deny"}:
                raise ValueError(
                    f"case {case.get('id')} has unsupported expectation "
                    f"{check.get('expect')!r}")


def _run_check(check: dict[str, Any]) -> RuleVerdict:
    check_type = check["type"]
    value = check["input"]
    if check_type == "intent":
        return screen_user_intent(value)
    if check_type == "command":
        return check_command(value)
    raise ValueError(f"unsupported check type: {check_type!r}")


def _matches(expect: str, actual: RuleDecision) -> bool:
    if expect == "deny":
        return actual == RuleDecision.DENY
    if expect == "allow":
        return actual == RuleDecision.ALLOW
    if expect == "review":
        return actual == RuleDecision.REVIEW
    if expect == "not_deny":
        return actual != RuleDecision.DENY
    raise ValueError(f"unsupported expectation: {expect!r}")


def evaluate_benchmark(data: dict[str, Any]) -> dict[str, Any]:
    validate_benchmark(data)
    case_results: list[CaseResult] = []

    for case in data["cases"]:
        checks: list[CheckResult] = []
        for check in case["checks"]:
            verdict = _run_check(check)
            passed = _matches(check["expect"], verdict.decision)
            checks.append(CheckResult(
                case_id=case["id"],
                suite=case["suite"],
                check_type=check["type"],
                input=check["input"],
                expect=check["expect"],
                actual=verdict.decision.value,
                passed=passed,
                reason=verdict.reason,
                matched_rule=verdict.matched_rule,
            ))
        case_results.append(CaseResult(
            id=case["id"],
            suite=case["suite"],
            category=case["category"],
            expected=case["expected"],
            passed=all(check.passed for check in checks),
            checks=checks,
        ))

    return _summarize(data, case_results)


def _summarize(data: dict[str, Any],
               case_results: list[CaseResult]) -> dict[str, Any]:
    by_suite: dict[str, list[CaseResult]] = defaultdict(list)
    for result in case_results:
        by_suite[result.suite].append(result)

    suites: dict[str, dict[str, Any]] = {}
    for suite, results in sorted(by_suite.items()):
        total = len(results)
        passed = sum(1 for result in results if result.passed)
        check_total = sum(len(result.checks) for result in results)
        check_passed = sum(1 for result in results
                           for check in result.checks if check.passed)
        denied_safe = sum(
            1 for result in results
            if suite == "safe_operation"
            for check in result.checks
            if check.expect in {"allow", "not_deny"} and check.actual == "deny"
        )
        suites[suite] = {
            "cases_total": total,
            "cases_passed": passed,
            "cases_failed": total - passed,
            "case_pass_rate": passed / total if total else 0.0,
            "checks_total": check_total,
            "checks_passed": check_passed,
            "checks_failed": check_total - check_passed,
        }
        if suite == "safe_operation":
            suites[suite]["false_block_checks"] = denied_safe
            suites[suite]["false_block_rate"] = (
                denied_safe / check_total if check_total else 0.0
            )

    failures = [
        {
            "id": result.id,
            "suite": result.suite,
            "category": result.category,
            "failed_checks": [
                {
                    "type": check.check_type,
                    "input": check.input,
                    "expect": check.expect,
                    "actual": check.actual,
                    "reason": check.reason,
                    "matched_rule": check.matched_rule,
                }
                for check in result.checks if not check.passed
            ],
        }
        for result in case_results if not result.passed
    ]

    return {
        "name": data.get("name", "KylinGuard OS Agent Security Benchmark"),
        "version": data.get("version"),
        "sources": data.get("sources", []),
        "total_cases": len(case_results),
        "total_passed": sum(1 for result in case_results if result.passed),
        "total_failed": sum(1 for result in case_results if not result.passed),
        "suites": suites,
        "failures": failures,
        "passed": not failures,
    }
