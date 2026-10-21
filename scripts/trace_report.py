# p50 / p95 over events.jsonl, as two markdown tables.
#
#   python scripts/trace_report.py                 # ./events.jsonl
#   python scripts/trace_report.py path/to/events.jsonl
#
# The first table is per model call, grouped by the model that answered; the
# second is per question, end to end, including the attempts that failed. The
# output pastes straight into the README.

import json
import sys
from pathlib import Path

DEFAULT = Path(__file__).resolve().parent.parent / "events.jsonl"


def percentile(values, q):
    """Linear interpolation between closest ranks (numpy's default)."""
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q / 100
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def load(path):
    records, skipped = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            skipped += 1
    return records, skipped


def _num(value):
    return "-" if value is None else f"{value:.0f}"


def _pair(rows, key):
    values = [r[key] for r in rows if r.get(key) is not None]
    return [_num(percentile(values, 50)), _num(percentile(values, 95))]


def _cost(rows):
    costs = [r.get("cost_usd") for r in rows]
    known = [c for c in costs if c is not None]
    if not known:
        return "unpriced"
    total = f"${sum(known):.4f}"
    return total if len(known) == len(costs) else f"{total} ({len(costs) - len(known)} unpriced)"


def _table(header, rows):
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def call_table(calls):
    header = ["model", "calls", "latency p50 ms", "latency p95 ms", "tokens in p50",
              "tokens in p95", "tokens out p50", "tokens out p95", "cost total",
              "grounded answers"]
    rows = []
    for model in sorted({c["model"] for c in calls}):
        group = [c for c in calls if c["model"] == model]
        answers = [c for c in group if c.get("kind") == "answer"]
        grounded = sum(1 for c in answers if c.get("grounded"))
        rows.append([model, str(len(group)), *_pair(group, "latency_ms"),
                     *_pair(group, "tokens_in"), *_pair(group, "tokens_out"),
                     _cost(group), f"{grounded} of {len(answers)}"])
    return _table(header, rows)


def turn_table(turns):
    header = ["questions", "latency p50 ms", "latency p95 ms", "tokens in p50",
              "tokens in p95", "tokens out p50", "tokens out p95", "cost total",
              "grounded"]
    grounded = sum(1 for t in turns if t.get("grounded"))
    row = [str(len(turns)), *_pair(turns, "latency_ms"), *_pair(turns, "tokens_in"),
           *_pair(turns, "tokens_out"), _cost(turns), f"{grounded} of {len(turns)}"]
    return _table(header, [row])


def main(argv):
    path = Path(argv[0]) if argv else DEFAULT
    if not path.exists():
        print(f"no trace file at {path} - run agent.py or server.py first", file=sys.stderr)
        return 1
    records, skipped = load(path)
    calls = [r for r in records if r.get("type") == "llm_call"]
    turns = [r for r in records if r.get("type") == "turn"]
    if not calls:
        print(f"no llm_call records in {path}", file=sys.stderr)
        return 1

    print(f"## Per model call ({len(calls)} calls)\n")
    print(call_table(calls))
    if turns:
        errors = sum(1 for t in turns if t.get("error"))
        print(f"\n## Per question ({len(turns)} questions, errors: {errors})\n")
        print(turn_table(turns))
    if skipped:
        print(f"\nskipped {skipped} unreadable line{'s' if skipped != 1 else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
