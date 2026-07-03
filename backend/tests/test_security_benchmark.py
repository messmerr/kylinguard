from pathlib import Path

from kylinguard.security_benchmark import evaluate_benchmark, load_benchmark


BENCHMARK = (
    Path(__file__).resolve().parents[2]
    / "benchmarks"
    / "os_agent_security_benchmark.json"
)


def test_security_benchmark_dataset_passes():
    report = evaluate_benchmark(load_benchmark(BENCHMARK))
    assert report["total_cases"] == 60
    assert report["suites"]["unsafe_block"]["cases_total"] == 21
    assert report["suites"]["safe_operation"]["cases_total"] == 24
    assert report["suites"]["critical_config_reliability"]["cases_total"] == 15
    assert report["passed"], report["failures"]
