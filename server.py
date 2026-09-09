# The web front end for Second Pass.
#
#   .\run.ps1 python server.py        # http://localhost:8080
#
# Every route below reaches ClickHouse the same way the agent does: by composing
# SQL from a template and handing it to the mcp-clickhouse MCP server. There is
# no second data path. If MCP is down, the page is empty rather than quietly
# falling back to something that works but proves nothing.
#
# The MCP session is opened once at startup, in the server's own event loop, and
# every request shares it. That is not just an optimisation — the session is
# owned by a single task by design (see agent.py), so it has to be started
# somewhere that outlives an individual request.

import asyncio
import io
import json
import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from agent import (
    FIELDS,
    MCP,
    MODEL,
    MODELS,
    _film,
    correlation_table,
    direction_agreement,
    negative_control,
    stream,
)

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


async def lifespan(app):
    # Pay the ClickHouse cold start before the first visitor, not during it — an
    # idle Cloud instance takes the better part of twenty seconds to accept its
    # first connection, and spending that inside a request looks like a hang.
    #
    # A failure here is logged and survived rather than raised. Uvicorn does not
    # bind the port until startup finishes, so raising would mean a ClickHouse
    # hiccup during a rollout fails the whole revision's health check; better to
    # come up and let the page report the error it gets.
    try:
        elapsed = await MCP.prewarm()
        print(f"[clickhouse ready in {elapsed:.1f}s]", flush=True)
    except Exception as exc:  # noqa: BLE001 - startup must not depend on this
        print(f"[clickhouse prewarm failed: {exc}]", flush=True)
    yield
    await MCP.aclose()


app = FastAPI(title="Second Pass", lifespan=lifespan)


class Question(BaseModel):
    question: str
    film: str | None = None


def bins_in(response):
    """Pull the segment numbers out of a tool's result, for the chart to mark.

    Driven by the query result rather than by the model's sentence about it. The
    chart then marks exactly the rows the database returned, whatever words the
    answer happens to use — and a tool with no bin column, like the correlation
    table, correctly marks nothing.
    """
    try:
        table = json.loads(response.get("result", ""))
        at = table["columns"].index("bin")
    except (ValueError, KeyError, TypeError, AttributeError):
        return []
    return sorted({row[at] for row in table.get("rows", [])})


@app.get("/", response_class=HTMLResponse)
async def index():
    return io.open(os.path.join(WEB, "index.html"), encoding="utf-8").read()


@app.get("/api/health")
async def health():
    return {
        "model": MODEL,
        "models": MODELS,
        "clickhouse": os.environ.get("CLICKHOUSE_HOST", "").split(".")[0],
        "mcp": "mcp-clickhouse",
        "revision": os.environ.get("K_REVISION", "local"),
    }


@app.get("/api/films")
async def films():
    """What is actually in the corpus, in the order the page should offer it."""
    return json.loads(await MCP.run_query(
        "SELECT film, title, youtube_id, round(duration_s) AS duration_s, bin_count, "
        "round(position_bias, 2) AS position_bias FROM films ORDER BY position_bias DESC"
    ))


@app.get("/api/film/{film}")
async def film(film: str):
    """Everything the chart and the hover card need, in one query.

    Edge bins are returned rather than filtered. They are drawn differently and
    excluded from claims, but hiding them would mean the page shows a cleaner
    curve than the data actually is. The same goes for the end credits: on Tears
    of Steel they carry the upload's highest attention, and a page that dropped
    them would look like a film whose audience simply builds to the end.
    """
    try:
        name = _film(film)
    except ValueError as exc:
        raise HTTPException(404, str(exc))

    sql = (
        "SELECT bin, start_s, end_s, "
        "round(attention_raw, 4) AS raw, round(attention_base, 4) AS base, "
        "round(attention_res, 4) AS res, is_edge, is_credits, "
        + ", ".join(sorted(FIELDS))
        + ", one_line "
        f"FROM segments WHERE film = '{name}' ORDER BY bin"
    )
    segments = json.loads(await MCP.run_query(sql))

    meta = json.loads(await MCP.run_query(
        "SELECT film, title, youtube_id, duration_s, bin_count, "
        f"round(position_bias, 2) AS position_bias FROM films WHERE film = '{name}'"
    ))
    if not meta["rows"]:
        raise HTTPException(404, f"{name} is not loaded")

    return {
        "film": dict(zip(meta["columns"], meta["rows"][0])),
        "columns": segments["columns"],
        "rows": segments["rows"],
    }


@app.get("/api/proof/{film}")
async def proof(film: str, shuffled: bool = False):
    """The correlation table, or the same table computed against shuffled attention.

    Both are the tools the agent calls, not copies of them — so the panel and the
    answer cannot disagree about what the database said.
    """
    try:
        table = negative_control(film) if shuffled else correlation_table(film)
        return json.loads(await table)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@app.get("/api/agreement")
async def agreement():
    """Cross-film direction agreement — the same tool the agent calls.

    The page used to compute this itself, in JavaScript, against one other film
    while the agent's tool compared all of them. Two implementations of one
    statistic is how a page and an agent end up telling a visitor different
    things about the same corpus.
    """
    return json.loads(await direction_agreement())


@app.post("/api/ask")
async def ask(body: Question):
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "empty question")

    # Validated, because it reaches a prompt and an allow-list check either way.
    film = None
    if body.film:
        try:
            film = _film(body.film)
        except ValueError:
            film = None

    async def events():
        try:
            async for kind, a, b in stream(question, film):
                if kind == "tool":
                    payload = {"type": "tool", "name": a, "args": b}
                elif kind == "result":
                    payload = {"type": "bins", "bins": bins_in(b)}
                elif kind == "model":
                    payload = {"type": "model", "name": a}
                else:
                    payload = {"type": "answer", "text": a}
                yield "data: " + json.dumps(payload) + "\n\n"
        except Exception as exc:  # noqa: BLE001 - surfaced to the page
            # agent.py already retried the transient ones. Anything reaching
            # here is worth showing, but a raw provider traceback where the
            # answer goes is not an explanation - say which side failed.
            raw = str(exc)
            friendly = (
                "The model is refusing requests right now (upstream 503 after "
                "retries). The data is fine - reload and ask again."
                if any(m in raw for m in ("503", "UNAVAILABLE", "high demand"))
                else raw
            )
            yield "data: " + json.dumps(
                {"type": "error", "text": friendly}
            ) + "\n\n"
        yield "data: " + json.dumps({"type": "done"}) + "\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8080)),
        log_level="warning",
    )
