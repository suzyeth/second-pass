import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "trace_report.py"
spec = importlib.util.spec_from_file_location("trace_report", SCRIPT)
trace_report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trace_report)


def test_percentile_interpolates_between_ranks():
    values = [10, 20, 30, 40]
    assert trace_report.percentile(values, 50) == pytest.approx(25)
    assert trace_report.percentile(values, 95) == pytest.approx(38.5)
    assert trace_report.percentile([7], 95) == 7
    assert trace_report.percentile([], 50) is None


def write(path, records):
    path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n" + "not json\n", encoding="utf-8"
    )


def call(model, latency, tin, tout, cost, grounded, kind="answer"):
    return {"type": "llm_call", "model": model, "kind": kind, "latency_ms": latency,
            "tokens_in": tin, "tokens_out": tout, "cost_usd": cost, "grounded": grounded}


def turn(latency, tin, tout, cost, grounded, error=None):
    return {"type": "turn", "model": "m", "latency_ms": latency, "tokens_in": tin,
            "tokens_out": tout, "cost_usd": cost, "grounded": grounded, "error": error}


def test_report_prints_p50_p95_per_model_and_per_turn(tmp_path, capsys):
    path = tmp_path / "events.jsonl"
    write(path, [
        call("m", 100, 1000, 10, 0.001, False, kind="tool_call"),
        call("m", 300, 3000, 30, 0.003, True),
        turn(1000, 4000, 40, 0.004, True),
        turn(3000, 5000, 50, None, False, error="RuntimeError: 503"),
    ])

    assert trace_report.main([str(path)]) == 0
    out = capsys.readouterr().out

    assert "Per model call" in out and "Per question" in out
    assert "| m |" in out
    assert "| 200 | 290 |" in out          # call latency p50 / p95
    assert "| 2000 | 2900 |" in out        # turn latency p50 / p95
    assert "1 of 1" in out                 # grounded answer calls
    assert "1 of 2" in out                 # grounded turns
    assert "errors: 1" in out
    assert "skipped 1 unreadable line" in out


def test_report_on_missing_file_fails_clearly(tmp_path, capsys):
    assert trace_report.main([str(tmp_path / "nope.jsonl")]) == 1
    assert "no trace file" in capsys.readouterr().err


def test_report_with_no_calls_says_so(tmp_path, capsys):
    path = tmp_path / "events.jsonl"
    path.write_text("", encoding="utf-8")
    assert trace_report.main([str(path)]) == 1
    assert "no llm_call records" in capsys.readouterr().err
