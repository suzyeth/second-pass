import asyncio
import json
from types import SimpleNamespace

import pytest

import agent
import tracing


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def usage(prompt, candidates, thoughts=None):
    return SimpleNamespace(
        prompt_token_count=prompt,
        candidates_token_count=candidates,
        thoughts_token_count=thoughts,
    )


def event(parts, usage_metadata=None, final=False):
    return SimpleNamespace(
        content=SimpleNamespace(parts=parts),
        usage_metadata=usage_metadata,
        is_final_response=lambda: final,
    )


def call_part(name):
    return SimpleNamespace(function_call=SimpleNamespace(name=name, args={}))


def response_part(name):
    return SimpleNamespace(
        function_call=None,
        function_response=SimpleNamespace(name=name, response={"result": "{}"}),
    )


def text_part(text):
    return SimpleNamespace(function_call=None, function_response=None, text=text)


def read(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_cost_uses_the_price_table_and_is_none_for_unknown_models():
    table = {"m": (1.0, 4.0)}
    assert tracing.cost_usd("m", 1_000_000, 500_000, table) == pytest.approx(3.0)
    assert tracing.cost_usd("unpriced", 10, 10, table) is None


def test_price_table_can_be_extended_from_the_environment(monkeypatch):
    monkeypatch.setenv("TRACE_PRICES", json.dumps({"gemini-x": [0.5, 1.5]}))
    table = tracing.price_table()
    assert table["gemini-x"] == (0.5, 1.5)
    assert "gemini-2.5-flash" in table


def test_bad_price_override_is_rejected(monkeypatch):
    monkeypatch.setenv("TRACE_PRICES", "{not json")
    with pytest.raises(ValueError, match="TRACE_PRICES"):
        tracing.price_table()


def test_usage_counts_thinking_tokens_as_output():
    assert tracing.usage_of(event([], usage(120, 30, 50))) == (120, 80)
    assert tracing.usage_of(event([], usage(120, None))) == (120, 0)
    assert tracing.usage_of(event([])) is None


def test_turn_records_each_model_call_and_the_turn(tmp_path):
    path = tmp_path / "events.jsonl"
    clock = Clock()
    trace = tracing.TurnTrace("q?", "m", path=path, clock=clock, prices={"m": (1.0, 2.0)})

    clock.now += 0.4
    trace.observe(event([call_part("correlation_table")], usage(1000, 20)))
    clock.now += 1.5  # the tool runs; this is not model latency
    trace.observe(event([response_part("correlation_table")]))
    clock.now += 0.9
    trace.observe(event([text_part("answer")], usage(3000, 200, 100), final=True))
    trace.finish()

    calls, turn = read(path)[:2], read(path)[2]
    assert [c["type"] for c in calls] == ["llm_call", "llm_call"]
    assert [c["kind"] for c in calls] == ["tool_call", "answer"]
    assert [c["latency_ms"] for c in calls] == [400, 900]
    assert [c["tokens_in"] for c in calls] == [1000, 3000]
    assert [c["tokens_out"] for c in calls] == [20, 300]
    assert [c["grounded"] for c in calls] == [False, True]
    assert calls[1]["cost_usd"] == pytest.approx((3000 * 1 + 300 * 2) / 1e6)

    assert turn["type"] == "turn"
    assert turn["llm_calls"] == 2
    assert turn["tool_results"] == 1
    assert turn["latency_ms"] == 2800
    assert turn["tokens_in"] == 4000 and turn["tokens_out"] == 320
    assert turn["grounded"] is True
    assert turn["error"] is None
    assert len({r["run_id"] for r in read(path)}) == 1


def test_answer_without_a_tool_result_is_not_grounded(tmp_path):
    path = tmp_path / "events.jsonl"
    trace = tracing.TurnTrace("q?", "m", path=path, clock=Clock())
    trace.observe(event([text_part("from memory")], usage(10, 5), final=True))
    trace.finish()
    call, turn = read(path)
    assert call["grounded"] is False
    assert call["cost_usd"] is None
    assert turn["grounded"] is False


def test_failed_turn_is_recorded_with_its_error(tmp_path):
    path = tmp_path / "events.jsonl"
    trace = tracing.TurnTrace("q?", "m", path=path, clock=Clock())
    trace.finish(error=RuntimeError("503 UNAVAILABLE"))
    (turn,) = read(path)
    assert turn["error"] == "RuntimeError: 503 UNAVAILABLE"
    assert turn["grounded"] is False


def test_empty_trace_path_disables_writing(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACE_PATH", "")
    assert tracing.trace_path() is None
    trace = tracing.TurnTrace("q?", "m", path=None, clock=Clock())
    trace.observe(event([text_part("a")], usage(1, 1), final=True))
    trace.finish()
    assert list(tmp_path.iterdir()) == []


def test_agent_stream_writes_trace(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("TRACE_PATH", str(path))

    scripted = [
        event([call_part("underperforming_stretches")], usage(800, 15)),
        event([response_part("underperforming_stretches")]),
        event([text_part("Bin 8 underperforms.")], usage(2400, 120), final=True),
    ]

    class FakeRunner:
        def __init__(self, agent, app_name):
            self.session_service = SimpleNamespace(create_session=self._session)

        async def _session(self, app_name, user_id):
            return SimpleNamespace(id="s")

        async def run_async(self, user_id, session_id, new_message):
            for item in scripted:
                yield item

    monkeypatch.setattr(agent, "InMemoryRunner", FakeRunner)

    async def collect():
        return [item async for item in agent.stream("which stretches lose people?")]

    items = asyncio.run(collect())
    assert items[-1] == ("answer", "Bin 8 underperforms.", None)

    records = read(path)
    assert [r["type"] for r in records] == ["llm_call", "llm_call", "turn"]
    assert all(r["model"] == agent.MODELS[0] for r in records)
    assert records[-1]["grounded"] is True
    assert records[-1]["tokens_in"] == 3200
