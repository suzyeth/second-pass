# The agent: Gemini through Google ADK, every answer grounded in a ClickHouse
# query executed by the official mcp-clickhouse MCP server.
#
#   python agent.py "which stretches lose people, and what is on screen there?"
#
# The design decision that matters
# --------------------------------
# The model is NOT given a free-form SQL tool. It is given four questions it can
# ask, and it fills in their parameters. The SQL is composed here, from templates,
# and then handed to MCP's run_query.
#
# That is not belt-and-braces caution, it is the only arrangement that satisfies
# both constraints at once. The track requires ClickHouse to be reached at
# runtime through the MCP server — so a local SQL client would not count. And a
# model improvising ClickHouse dialect live on camera is the single most likely
# thing to fail in an unedited demo. Templates through MCP gives a deterministic
# query and a genuine MCP call path; the server log shows the statement.
#
# Every parameter is validated against an allow-list before it reaches a string
# format, because "the model chooses the film name" and "the model writes the
# WHERE clause" are one careless f-string apart.

import asyncio
import json
import os
import sys
import time

from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from google.genai import types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
NEWLINE = chr(10)

# Allow-lists. A value not in one of these never reaches a query.
#
# FILMS is what the corpus may ever contain; LOADED is what is actually in the
# database right now, read at startup. Validating against the first alone let the
# model sweep all three films and get NaN back from two of them, which it reported
# honestly and which should never have been askable in the first place.
FILMS = {"tos", "bbb", "sintel"}
LOADED = set()
FIELDS = {
    "visual_event_density",
    "story_information",
    "character_presence",
    "speech_density",
    "score_intensity",
    "inertness",
}


class ClickHouseMCP:
    """One long-lived stdio session to mcp-clickhouse, owned by one task.

    The task ownership is not decoration. ADK runs each tool call in its own
    task, so an AsyncExitStack entered during the first tool call gets exited
    from a different task at shutdown, and anyio raises "Attempted to exit
    cancel scope in a different task than it was entered in" — after a correct
    answer has already been printed, which is the worst possible time to crash
    on camera. So: one task opens the session, serves queries off a queue, and
    closes it. Enter and exit happen in the same place.
    """

    def __init__(self):
        self._queue = None
        self._task = None
        self._up = None

    async def _serve(self):
        params = StdioServerParameters(
            command="python",
            args=["-m", "mcp_clickhouse.main"],
            env={
                **os.environ,
                "CLICKHOUSE_HOST": os.environ["CLICKHOUSE_HOST"],
                "CLICKHOUSE_PORT": os.environ.get("CLICKHOUSE_PORT", "8443"),
                "CLICKHOUSE_USER": os.environ.get("CLICKHOUSE_USER", "default"),
                "CLICKHOUSE_PASSWORD": os.environ["CLICKHOUSE_PASSWORD"],
                "CLICKHOUSE_SECURE": "true",
                # The ASCII art is noise; the per-query log lines below it are
                # the evidence that MCP is on the path, and those stay.
                "FASTMCP_SHOW_CLI_BANNER": "false",
            },
        )

        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._up.set_result(None)

                    while True:
                        item = await self._queue.get()
                        if item is None:
                            return
                        sql, future = item
                        try:
                            result = await session.call_tool("run_query", {"query": sql})
                            future.set_result(str(result.content[0].text))
                        except Exception as exc:  # noqa: BLE001 - relayed to caller
                            future.set_exception(exc)
        except Exception as exc:  # noqa: BLE001 - startup failure
            if not self._up.done():
                self._up.set_exception(exc)
            raise

    async def start(self):
        if self._task is not None:
            return
        loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        self._up = loop.create_future()
        self._task = loop.create_task(self._serve())
        await self._up

    async def run_query(self, sql):
        await self.start()
        future = asyncio.get_running_loop().create_future()
        await self._queue.put((sql, future))
        return await future

    async def prewarm(self):
        """Pay the cold-start cost before the demo starts talking.

        A ClickHouse Cloud instance that has been idle takes about eighteen
        seconds to accept its first connection. Spending that during an
        establishing shot is fine; spending it after a question has been asked
        looks like the agent has hung.
        """
        started = time.monotonic()
        catalog = json.loads(await self.run_query("SELECT film FROM films ORDER BY film"))
        LOADED.clear()
        LOADED.update(row[0] for row in catalog["rows"])
        return time.monotonic() - started

    async def aclose(self):
        if self._task is None:
            return
        await self._queue.put(None)
        await self._task
        self._task = self._queue = self._up = None


MCP = ClickHouseMCP()


def _film(value):
    allowed = LOADED or FILMS
    if value not in allowed:
        raise ValueError(f"{value!r} is not in the corpus; loaded: {sorted(allowed)}")
    return value


def _field(value):
    if value not in FIELDS:
        raise ValueError(f"unknown field {value!r}; known: {sorted(FIELDS)}")
    return value


def _limit(value, cap=10):
    return max(1, min(int(value), cap))


# --- the four questions ------------------------------------------------------


# The scored segments of one film, minus the edge bins whose residual is an
# artifact of a one-sided baseline window.
def _population(film):
    return (
        "SELECT *, row_number() OVER (ORDER BY bin) - 1 AS r, count() OVER () AS n "
        f"FROM segments WHERE film = '{film}' AND is_edge = 0"
    )


def _correlation_select(field, film, shift=None):
    """One feature's row of the correlation table.

    With shift set, the feature is paired against the attention of a DIFFERENT
    segment — bin r against bin (r * shift + 11) mod n. Because shift is coprime
    with n that is a genuine permutation, and because it is fixed rather than
    random the control gives the same numbers on every take. A negative control
    that moves between runs is not a control.
    """
    pop = _population(film)
    if shift is None:
        src, res, raw, pos = f"({pop})", "attention_res", "attention_raw", "bin"
    else:
        src = f"({pop}) AS a INNER JOIN ({pop}) AS b ON b.r = (a.r * {shift} + 11) % a.n"
        field, res, raw, pos = f"a.{field}", "b.attention_res", "b.attention_raw", "b.bin"

    label = field.split(".")[-1]
    return (
        f"SELECT '{label}' AS feature, "
        f"round(rankCorr({field}, {raw}), 3) AS vs_raw, "
        f"round(rankCorr({field}, {res}), 3) AS vs_residual, "
        f"round(rankCorr({field}, {pos}), 3) AS vs_position, "
        "count() AS n, round(1.96 / sqrt(count() - 1), 3) AS significant_above "
        f"FROM {src}"
    )


async def underperforming_stretches(film: str, limit: int = 5) -> str:
    """Stretches watched LESS than their position in the film predicts, with what is on screen.

    This is the only honest way to ask "where does this film lose people": raw
    attention rises toward the end of every film, so the raw minimum is just the
    middle. The residual is raw minus a local baseline.

    The result ALREADY contains what is on screen in each stretch (one_line) and
    its density scores. Do not follow up with content_at for the rows it returns.

    Args:
        film: which film. Your instructions name the ones in the corpus.
        limit: how many stretches, at most 10.
    """
    sql = (
        "SELECT bin, round(start_s) AS at_second, round(attention_res, 3) AS underperformance, "
        "visual_event_density, speech_density, one_line "
        f"FROM segments WHERE film = '{_film(film)}' AND is_edge = 0 "
        f"ORDER BY attention_res ASC LIMIT {_limit(limit)}"
    )
    return await MCP.run_query(sql)


async def content_at(film: str, bin: int) -> str:
    """Every column for ONE segment, when a single moment needs the full picture.

    Use this only when a specific bin has come up and its complete numbers matter
    — not to expand rows another tool already returned with their content attached.

    Args:
        film: which film. Your instructions name the ones in the corpus.
        bin: the segment index, 0-99.
    """
    sql = (
        "SELECT bin, round(start_s) AS at_second, round(attention_raw,3) AS raw, "
        "round(attention_base,3) AS expected_for_position, round(attention_res,3) AS residual, "
        "visual_event_density, story_information, character_presence, speech_density, "
        "score_intensity, inertness, one_line "
        f"FROM segments WHERE film = '{_film(film)}' AND bin = {max(0, min(int(bin), 99))}"
    )
    return await MCP.run_query(sql)


async def correlation_table(film: str) -> str:
    """All six content features against attention at once, with the noise floor.

    The one query that contains the whole argument. Each row gives a feature's
    rank correlation against raw attention, against the position-corrected
    residual, and against position itself, plus significant_above — the value
    a correlation has to beat at this sample size to mean anything at all.

    Read it by comparing vs_raw to vs_position. Where they are close and
    vs_residual is under significant_above, the feature has no demonstrated
    relationship with attention; it only shares a trend with where it sits.

    This covers every feature there is. Nothing else needs to be queried to
    answer a question about which features relate to attention.

    Args:
        film: which film. Your instructions name the ones in the corpus.
    """
    f = _film(film)
    parts = [_correlation_select(field, f) for field in sorted(FIELDS)]
    # The verdict is computed in SQL, not left to the model. Asked in prose to
    # call 0.224 against a 0.215 floor "marginal", the model reports it as a
    # finding instead — the threshold clears, so the caveat evaporates. A column
    # cannot be talked around.
    return await MCP.run_query(_with_verdicts(parts))


def _with_verdicts(parts):
    return (
        "SELECT feature, vs_raw, vs_residual, vs_position, n, significant_above, "
        "multiIf(abs(vs_residual) < significant_above, 'no relationship shown', "
        "abs(vs_residual) < significant_above * 1.5, "
        "'marginal - clears the floor by too little to believe from one film', "
        "'holds up after correcting for position') AS verdict, "
        "multiIf(abs(vs_raw) > significant_above AND abs(vs_residual) < significant_above, "
        "'yes - looks real against raw attention, is not', 'no') AS position_artifact "
        "FROM (" + " UNION ALL ".join(parts) + ") ORDER BY abs(vs_raw) DESC"
    )


async def negative_control(film: str) -> str:
    """The same correlation table, computed against SHUFFLED attention.

    Each feature is paired with another segment's attention instead of its own.
    Whatever the real table finds, this one must find nothing — and if it does
    find something, the method is producing structure out of noise and no result
    from it can be trusted.

    Run it when asked whether the findings are real, or whether the method works.

    Args:
        film: which film. Your instructions name the ones in the corpus.
    """
    f = _film(film)
    return await MCP.run_query(
        _with_verdicts([_correlation_select(field, f, shift=37) for field in sorted(FIELDS)]))


async def exit_hotspots(film: str, limit: int = 5) -> str:
    """Where sessions stop, as a RATE rather than a count, joined to what was playing.

    Counting exits per segment answers the wrong question. Every session starts at
    the beginning, so more sessions are alive early and the raw count peaks in the
    first minute of any film — survivorship wearing the costume of a finding, the
    same trick position plays on attention. So this divides exits by the number of
    sessions that actually reached the segment.

    Aggregates several million event rows against the segment table.

    Two things must be said about any answer built on this. The events are
    SYNTHETIC. And they were generated FROM the attention curve, so their ranking
    follows it by construction — this demonstrates the query and the survivorship
    correction, it is not independent evidence about the film. Evidence about the
    film comes from the segment columns.

    Args:
        film: which film. Your instructions name the ones in the corpus.
        limit: how many segments, at most 10.
    """
    sql = (
        "SELECT e.bin AS bin, round(s.start_s) AS at_second, "
        "countIf(e.event = 'exit') AS exits, "
        "countIf(e.event IN ('watch', 'exit')) AS reached, "
        # An integer, not a rate like 0.0241. Asked to read four-decimal figures
        # back in prose, the model transposed 0.024 into 0.040 — a digit slip
        # that is invisible in review and fatal in a recorded demo. Numbers the
        # model has to restate should be shaped so they cannot be garbled.
        "round(countIf(e.event = 'exit') / countIf(e.event IN ('watch', 'exit')) * 10000) "
        "AS exits_per_10k_reached, "
        "round(s.attention_raw, 3) AS attention_raw, s.one_line AS one_line "
        "FROM playback_events AS e INNER JOIN segments AS s "
        "ON e.film = s.film AND e.bin = s.bin "
        f"WHERE e.film = '{_film(film)}' AND s.is_edge = 0 "
        "GROUP BY bin, at_second, attention_raw, one_line "
        f"ORDER BY exits_per_10k_reached DESC LIMIT {_limit(limit)}"
    )
    return await MCP.run_query(sql)


INSTRUCTION = """You answer questions about why an audience's attention rises and
falls across a film, using a database of measured segments and playback events.

Ground every claim in a tool result. If you did not query it, do not say it.

The one thing you must never get wrong: attention rises toward the end of almost
every film regardless of content. A correlation against RAW attention is therefore
usually measuring position. When correlation_table shows vs_raw and vs_position
are both large and vs_residual is small, say plainly that the apparent relationship
is explained by position and is not evidence about content.

Report weak results as weak. The correlation tools return significant_above:
any correlation smaller than that is indistinguishable from noise at this sample
size, and must be described that way. Use the returned number, never a remembered
one. Saying a result is too small to mean anything is more useful than finding a
story in it.

The playback events are synthetic AND derived from the attention curve, so they
cannot corroborate anything the curve already says. Whenever you use exit_hotspots,
say both: that the events are synthetic, and that they are not independent evidence.
Segment columns are real measurements and carry the actual claims.

Each row carries a verdict column and a position_artifact column, both computed
from the data. Report what they say. Do not upgrade a verdict of "marginal" into
a finding, and do not file a marginal result under the same heading as one that
holds up.

Refer to a film by its id, exactly as given. Do not translate an id into a title
— you have not been told what these films are called, and guessing produces a
confident wrong name attached to correct numbers.

Plain text only. No LaTeX, no dollar signs around numbers, no backslash commands
— the answer is read in a terminal and in a web panel, and both render them raw.

Attention here is a normalised rewatch score, never a count of people. Do not
describe it as viewers, counts, or numbers watching. Only exit_hotspots counts
sessions, and those are synthetic.

When several rows share a verdict, say they share the verdict. Do not claim they
share a shape their numbers do not: three features can all be position artifacts
while only two of them correlate more strongly with position than with attention.
A generalisation no single row supports is the same error as an unsourced claim.

Write for the person who cut the film, not for whoever built the database. Never
print a column name. Say what the number measures: "correlates 0.366 with how much
a segment was watched, but 0.377 with how late it falls in the film" — not "vs_raw
is 0.366, vs_position is 0.377". The caveats stay; the vocabulary changes.

At most three short paragraphs. Numbers and specifics, not adjectives."""


def build_agent():
    """The agent, built fresh per question but from one definition.

    The CLI and the web server both come through here, so there is no second
    copy of the tool list or the instruction to drift out of sync.
    """
    corpus = ", ".join(sorted(LOADED)) or "nothing yet"
    return LlmAgent(
        name="second_pass",
        model=MODEL,
        description="Answers questions about attention and content across a film corpus.",
        instruction=INSTRUCTION + f"{NEWLINE}{NEWLINE}The corpus holds exactly one "
        f"film id per entry, and right now that is: {corpus}. Never query any other.",
        tools=[
            underperforming_stretches,
            content_at,
            correlation_table,
            negative_control,
            exit_hotspots,
        ],
    )


async def stream(question):
    """Yield ("tool", name, args) as each query is issued, then ("answer", text).

    Also yields ("result", name, {"result": <json string>}) for each tool return,
    so the caller can drive the interface from what the database actually said
    rather than from the model's prose about it. Parsing the answer text for
    segment references was the first attempt and it failed the way that always
    fails: the model says "bin 8" one run, "segment 8" the next, and "fifty-nine
    seconds in" the run after that.

    The tool events are not progress decoration. They are the audit trail: the
    viewer sees which question was asked of the database before they see the
    sentence built on it, which is the difference between a grounded answer and
    one that merely sounds grounded.
    """
    runner = InMemoryRunner(agent=build_agent(), app_name="second-pass")
    session = await runner.session_service.create_session(
        app_name="second-pass", user_id="local"
    )
    message = types.Content(role="user", parts=[types.Part(text=question)])

    async for event in runner.run_async(
        user_id="local", session_id=session.id, new_message=message
    ):
        for part in (event.content.parts if event.content else []) or []:
            if getattr(part, "function_call", None):
                call = part.function_call
                yield ("tool", call.name, dict(call.args))
            elif getattr(part, "function_response", None):
                got = part.function_response
                yield ("result", got.name, dict(got.response or {}))
            elif getattr(part, "text", None) and event.is_final_response():
                yield ("answer", part.text.strip(), None)


async def ask(question):
    elapsed = await MCP.prewarm()
    print(f"[clickhouse ready in {elapsed:.1f}s]")
    try:
        async for kind, a, b in stream(question):
            if kind == "result":
                continue
            if kind == "tool":
                print(f"  -> {a}({b})")
            else:
                print()
                print(a)
    finally:
        await MCP.aclose()


if __name__ == "__main__":
    question = " ".join(sys.argv[1:]) or "Which stretches of tos lose people, and what is on screen there?"
    print(f"Q: {question}\n")
    asyncio.run(ask(question))
