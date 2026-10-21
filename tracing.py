# Model-call tracing: one JSON line per Gemini call, one per question.
#
# The agent's claims are audited by the tool rows the viewer sees; its cost and
# speed were not audited by anything. "It answers in a few seconds" is the same
# kind of sentence as "the control confirms this" — true or not, nobody measured
# it. So every model call writes what it took (tokens in and out, latency, list
# price) and whether a tool result had come back before it answered, to
# events.jsonl. scripts/trace_report.py turns that file into p50 / p95.
#
# Latency here is the model's, not the turn's: the clock restarts after every
# event, so the seconds a ClickHouse query spends running are charged to the
# tool, not to the call that comes after it. The per-question record carries the
# end-to-end figure separately.

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone

# USD per million tokens, (input, output), Gemini API paid-tier list prices for
# text, read from https://ai.google.dev/gemini-api/docs/pricing on 2026-10-21.
# gemini-3.5-flash was not on that page, so it is traced with cost_usd = null
# rather than a guessed figure; add a model through
# TRACE_PRICES='{"model": [in, out]}' once its price is published.
PRICES_PER_MTOK = {
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
}

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "events.jsonl")
QUESTION_CHARS = 200


def trace_path():
    """Where traces go. TRACE_PATH overrides; set it empty to turn tracing off."""
    path = os.environ.get("TRACE_PATH")
    if path is None:
        return DEFAULT_PATH
    return path or None


def price_table():
    table = dict(PRICES_PER_MTOK)
    raw = os.environ.get("TRACE_PRICES")
    if not raw:
        return table
    try:
        extra = json.loads(raw)
        table.update({str(k): (float(v[0]), float(v[1])) for k, v in extra.items()})
    except (ValueError, TypeError, IndexError, AttributeError) as exc:
        raise ValueError(
            'TRACE_PRICES must be JSON like {"model": [input_usd, output_usd]}'
        ) from exc
    return table


def cost_usd(model, tokens_in, tokens_out, table=None):
    price = (table if table is not None else price_table()).get(model)
    if price is None:
        return None
    return round((tokens_in * price[0] + tokens_out * price[1]) / 1_000_000, 8)


def usage_of(event):
    """(tokens_in, tokens_out) for an event that came from a model call, else None.

    Thinking tokens are billed as output, so they are counted as output.
    """
    meta = getattr(event, "usage_metadata", None)
    if meta is None:
        return None
    tokens_in = meta.prompt_token_count or 0
    tokens_out = (meta.candidates_token_count or 0) + (
        getattr(meta, "thoughts_token_count", None) or 0
    )
    return tokens_in, tokens_out


def _parts(event):
    content = getattr(event, "content", None)
    return (content.parts if content else None) or []


def _kind(event):
    parts = _parts(event)
    if any(getattr(p, "function_call", None) for p in parts):
        return "tool_call"
    if event.is_final_response() and any(getattr(p, "text", None) for p in parts):
        return "answer"
    return "other"


def append(path, record):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


class TurnTrace:
    """The trace of one question: fed every runner event, written as it goes."""

    def __init__(self, question, model, film=None, path=None, clock=time.monotonic,
                 prices=None):
        self.run_id = uuid.uuid4().hex[:12]
        self.question = question[:QUESTION_CHARS]
        self.model = model
        self.film = film
        self.path = path
        self.clock = clock
        self.prices = prices if prices is not None else price_table()
        self.started = self.mark = clock()
        self.calls = []
        self.tool_results = 0

    def _base(self, kind):
        return {
            "type": kind,
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "run_id": self.run_id,
            "model": self.model,
        }

    def _write(self, record):
        if not self.path:
            return
        try:
            append(self.path, record)
        except OSError as exc:
            # A full disk or a read-only mount must not take an answer down with it.
            print(f"[trace not written: {exc}]", file=sys.stderr, flush=True)

    def observe(self, event):
        now = self.clock()
        usage = usage_of(event)
        if usage is not None:
            tokens_in, tokens_out = usage
            record = {
                **self._base("llm_call"),
                "call": len(self.calls) + 1,
                "kind": _kind(event),
                "latency_ms": round((now - self.mark) * 1000),
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost_usd": cost_usd(self.model, tokens_in, tokens_out, self.prices),
                # Whether the database had answered anything before the model
                # spoke. For a final answer, False means it was written from
                # nothing it queried.
                "grounded": self.tool_results > 0,
            }
            self.calls.append(record)
            self._write(record)
        self.tool_results += sum(
            1 for p in _parts(event) if getattr(p, "function_response", None)
        )
        self.mark = now

    def finish(self, error=None):
        costs = [c["cost_usd"] for c in self.calls]
        answered = any(c["kind"] == "answer" for c in self.calls)
        self._write({
            **self._base("turn"),
            "film": self.film,
            "question": self.question,
            "latency_ms": round((self.clock() - self.started) * 1000),
            "llm_calls": len(self.calls),
            "tool_results": self.tool_results,
            "tokens_in": sum(c["tokens_in"] for c in self.calls),
            "tokens_out": sum(c["tokens_out"] for c in self.calls),
            "cost_usd": None if None in costs or not costs else round(sum(costs), 8),
            "grounded": error is None and answered and self.tool_results > 0,
            "error": None if error is None else f"{type(error).__name__}: {error}"[:300],
        })
