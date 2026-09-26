"""scripts/pr_report.py: the numbers the profile and benchmark comments show."""

import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "pr_report.py"
spec = importlib.util.spec_from_file_location("pr_report", SCRIPT)
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def _frame(label: str, y: int, x: int, w: int) -> str:
    return (
        f"<g><title>{label} ({w} samples, 1%)</title>"
        f'<rect x="0%" y="{y}" width="1%" height="15" fg:x="{x}" fg:w="{w}"/></g>'
    )


# all -> thread -> serve -> (parse 6, sign 2): serve does 2 samples of its own.
SVG = "".join([
    _frame("all", 52, 0, 10),
    _frame("thread (1)", 68, 0, 10),
    _frame("serve (/x/kubed/selenium_flow/http/admin.py:1)", 84, 0, 10),
    _frame("parse (/x/site-packages/yaml/loader.py:9)", 100, 0, 6),
    _frame("sign (/x/kubed/selenium_flow/http/links.py:2)", 100, 6, 2),
])


def test_self_time_is_a_frame_less_what_it_called():
    """The entry points are under every sample; only self time finds where the
    work actually happened, and it has to come out of the geometry right."""
    total, frames = report.hot_frames(SVG)
    by_name = {name.split(" ")[0]: (own, every) for name, own, every in frames}
    assert total == 10
    assert by_name["serve"] == (2, 10)
    assert by_name["parse"] == (6, 6)
    assert by_name["sign"] == (2, 2)
    assert "all" not in by_name and "thread" not in by_name


def test_the_bench_table_compares_speed_with_main(tmp_path):
    current = tmp_path / "bench.json"
    current.write_text(json.dumps({"benchmarks": [
        {"fullname": "tests/bench/t.py::test_files", "stats": {"ops": 2000.0}},
        {"fullname": "tests/bench/t.py::test_new", "stats": {"ops": 10.0}},
    ]}))
    baseline = tmp_path / "base.json"
    baseline.write_text(json.dumps({"entries": {"Benchmark": [{"benches": [
        {"name": "tests/bench/t.py::test_files", "value": 1000.0},
    ]}]}}))
    table = report.bench(current, baseline)
    assert "| `test_files` | 500 µs | 1.00 ms | 2.00x |" in table
    assert "| `test_new` | 100.00 ms | — | new |" in table


def test_without_a_baseline_the_bench_table_says_so(tmp_path):
    current = tmp_path / "bench.json"
    current.write_text(json.dumps({"benchmarks": [
        {"fullname": "t.py::test_files", "stats": {"ops": 1000.0}},
    ]}))
    assert "No baseline from main yet" in report.bench(current, tmp_path / "none")
