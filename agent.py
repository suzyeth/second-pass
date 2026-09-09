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

# A chain, not a model. Two days before recording, gemini-3.5-flash-lite returned
# 503 "experiencing high demand" on four consecutive attempts over thirty-seven
# seconds — not a blip. A demo that claims to be unedited cannot depend on one
# upstream being healthy at the moment the camera rolls, so the retry ladder walks
# down this list. Which model actually answered is reported rather than assumed:
# the page updates its chip from the run, because a chip naming a model that did
# not answer is a small lie told on camera.
MODELS = [
    m for m in dict.fromkeys(
        # Measured 2026-09-04 on one full question: flash-lite 503, 2.5-flash
        # 4.4s, 3.5-flash 43s. The fallbacks are ordered by how fast they answer,
        # not by how new they are — a demo that recovers in seventeen seconds is
        # worth more than one that recovers in sixty with a shinier model.
        [os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite"),
         "gemini-2.5-flash",
         "gemini-3.5-flash"]
    ) if m
]
MODEL = MODELS[0]
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


def _avg_rank(column, alias):
    """Average rank, which is what Spearman actually requires.

    ClickHouse's rankCorr does not average tied ranks. The content scores are
    integers with seven to nine distinct values across eighty-four segments, so
    ties are not an edge case here, they are most of the data — and the two
    methods disagree by up to 0.083. That is the difference between a feature
    clearing the noise floor and not clearing it, which is the difference
    between this project reporting a finding and reporting none.

    rank() gives the minimum rank of a tie group (1, 1, 3, ...); adding half the
    group's excess turns it into the average.
    """
    return (
        f"rank() OVER (ORDER BY {column}) + "
        f"(count() OVER (PARTITION BY {column}) - 1) / 2 AS {alias}"
    )


def _pairs(field, film, shift):
    """One feature next to the attention it is being correlated against.

    With shift set, that is a DIFFERENT segment's attention — the negative
    control. Pairing happens before ranking, so the control ranks the same
    population the real table does.
    """
    pop = _population(film)
    if shift is None:
        return (
            f"SELECT {field} AS f, attention_raw AS a_raw, attention_res AS a_res, "
            f"bin AS a_pos FROM ({pop})"
        )
    return (
        f"SELECT a.{field} AS f, b.attention_raw AS a_raw, b.attention_res AS a_res, "
        f"b.bin AS a_pos FROM ({pop}) AS a INNER JOIN ({pop}) AS b "
        f"ON b.r = (a.r * {shift} + 11) % a.n"
    )


def _correlation_select(field, film, shift=None):
    """One feature's row of the correlation table: Spearman, three ways."""
    ranked = (
        "SELECT "
        + ", ".join([
            _avg_rank("f", "r_f"),
            _avg_rank("a_raw", "r_raw"),
            _avg_rank("a_res", "r_res"),
            _avg_rank("a_pos", "r_pos"),
        ])
        + " FROM (" + _pairs(field, film, shift) + ")"
    )
    # A feature that does not vary has no correlation, and ClickHouse says so
    # with NaN. Two things then go wrong. NaN is not valid JSON, so the web layer
    # 500s. And `abs(NaN) < floor` is false, so the verdict fell through every
    # branch to the last one and Big Buck Bunny - a film with no dialogue at all -
    # was reported as "speech density holds up after correcting for position".
    # A confident finding about a column of identical zeroes is precisely the
    # failure this project exists to catch, produced by this project.
    #
    # NULL instead, and a verdict that says undefined rather than nothing: "no
    # relationship shown" would claim a measurement that was never possible.
    varies = "uniqExact(r_f) > 1"
    return (
        f"SELECT '{field}' AS feature, "
        f"if({varies}, round(corr(r_f, r_raw), 3), NULL) AS vs_raw, "
        f"if({varies}, round(corr(r_f, r_res), 3), NULL) AS vs_residual, "
        f"if({varies}, round(corr(r_f, r_pos), 3), NULL) AS vs_position, "
        "count() AS n, "
        # Two floors, because there are two different questions. 1.96 is the
        # single-test threshold and answers "is THIS feature related to
        # attention". 2.6383 is the same threshold with Bonferroni applied over
        # the six features, and answers "did we find ANYTHING" — which is the
        # question actually being asked when all six are put on screen at once.
        # Judging six results against a one-test floor is how a null gets
        # reported as a marginal finding.
        "round(1.96 / sqrt(count() - 1), 3) AS significant_above_one_test, "
        "round(2.6383 / sqrt(count() - 1), 3) AS significant_above "
        f"FROM ({ranked})"
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
        # This tool answers about ONE bin by index, so it cannot filter the way
        # every other query does. A bin in the end credits still has scores - the
        # scorer was given the clip and did its job - and presenting them without
        # saying what they describe is how credits get discussed as if they were
        # film. The column says it instead.
        "multiIf(is_credits = 1, 'end credits - not film, excluded from every claim', "
        "is_edge = 1, 'edge bin - one-sided baseline, residual is a window artifact', "
        "'film') AS segment_kind, "
        "visual_event_density, story_information, character_presence, speech_density, "
        "score_intensity, inertness, one_line "
        f"FROM segments WHERE film = '{_film(film)}' AND bin = {max(0, min(int(bin), 99))}"
    )
    return await MCP.run_query(sql)


async def correlation_table(film: str) -> str:
    """All six content features against attention at once, with the noise floor.

    The one query that contains the whole argument. Each row gives a feature's
    rank correlation against raw attention, against the position-corrected
    residual, and against position itself, plus two noise floors.
    significant_above_one_test is the threshold for a single pre-chosen feature;
    significant_above is that threshold corrected for testing all six at once,
    and it is the one the verdict uses, because six features on screen at once
    is six tests. Both are optimistic: the residual is a moving-average residual
    and therefore serially correlated, so the effective sample size is below the
    row count. Read them as "at least this much noise".

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
        "SELECT feature, vs_raw, vs_residual, vs_position, n, "
        "significant_above_one_test, significant_above, "
        "multiIf(vs_residual IS NULL, "
        "'undefined - this feature does not vary in this film', "
        "abs(vs_residual) < significant_above, 'no relationship shown', "
        "abs(vs_residual) < significant_above * 1.5, "
        "'marginal - clears the floor by too little to believe from one film', "
        "'holds up after correcting for position') AS verdict, "
        "multiIf(vs_raw IS NULL OR vs_residual IS NULL, 'undefined', "
        "abs(vs_raw) > significant_above AND abs(vs_residual) < significant_above, "
        "'yes - looks real against raw attention, is not', 'no') AS position_artifact "
        "FROM (" + " UNION ALL ".join(parts) + ") ORDER BY vs_raw IS NULL, abs(vs_raw) DESC"
    )


# Exact small binomial coefficients, to row six. Six features is the most this
# can ever compare, so a literal Pascal's triangle beats a gamma-function
# approximation and cannot drift.
PASCAL = "[[1],[1,1],[1,2,1],[1,3,3,1],[1,4,6,4,1],[1,5,10,10,5,1],[1,6,15,20,15,6,1]]"


async def direction_agreement() -> str:
    """Whether the films in the corpus point the same way, and whether that means anything.

    Per-film sample size is capped near eighty-four because the heatmap is always
    exactly a hundred buckets whatever the runtime, so single-film significance
    at the effect sizes here is out of reach by construction. What is left to ask
    is whether separate films at least agree in direction.

    Returns each feature's residual correlation on every film side by side, and
    the two-tailed sign test over how many of them agree — computed in SQL, so
    the count and its p-value cannot come apart. Features that do not vary on
    some film are excluded, because a constant column has no direction.

    Use this for any question about whether the finding replicates, generalises,
    or holds across films. Do not answer such a question from two separate
    correlation tables by eye: five features agreeing looks like a result and is
    p = 0.06.
    """
    # Read the corpus from the database, not from the module global. LOADED is
    # filled by prewarm at startup, and prewarm is deliberately allowed to fail
    # without taking the service down — so an instance that started while
    # ClickHouse was suspended served every other route correctly and answered
    # this one with "only 0 films loaded". On the page that rendered as
    # "undefined of undefined ... p = NaN", and it went into a recording before
    # anyone noticed. Every other tool already falls back; this one asked a
    # cached global a question the database can answer.
    catalog = json.loads(await MCP.run_query("SELECT film FROM films ORDER BY film"))
    films = [row[0] for row in catalog["rows"]]
    LOADED.update(films)
    if len(films) < 2:
        return json.dumps({
            "columns": ["note"],
            "rows": [[f"only {len(films)} film loaded; agreement needs at least two"]],
        })

    parts = []
    for film in films:
        for field in sorted(FIELDS):
            ranked = (
                "SELECT " + _avg_rank(field, "r_f") + ", "
                + _avg_rank("attention_res", "r_res")
                + f" FROM ({_population(film)})"
            )
            parts.append(
                f"SELECT '{film}' AS film, '{field}' AS feature, "
                "if(uniqExact(r_f) > 1, round(corr(r_f, r_res), 3), NULL) AS vs_residual "
                f"FROM ({ranked})"
            )

    # direction and verdict are columns for the same reason every other judgement
    # here is: asked to read five sign pairs out of an array, the model put a
    # feature with two positive residuals in the negative group, and reported
    # p = 0.0625 without noticing it does not clear 0.05.
    sql = (
        # A bare [0.039, 0.048] does not say which film is which, and the model
        # read one straight past its own direction column and filed a feature
        # with two positive residuals under negative. Pair the numbers to their
        # films in the string itself; there is then nothing left to infer.
        "SELECT feature, "
        "multiIf(NOT same_direction, 'mixed', residuals[1] > 0, "
        "'positive on every film', 'negative on every film') AS direction, "
        "arrayStringConcat(arrayMap((f, v) -> concat(f, ' ', "
        "if(v > 0, '+', ''), toString(v)), films, residuals), ', ') AS residual_by_film, "
        "comparable, agreeing, sign_test_p, "
        # Three bands, not two. At p = 1.0 the earlier wording still said
        # "suggestive", which is the opposite of what a sign test returning 1.0
        # means: the directions are as consistent as coin flips.
        "multiIf(sign_test_p > 0.5, "
        "'no agreement beyond chance - the directions are as consistent as coin flips', "
        "sign_test_p > 0.05, "
        "'suggestive, not significant - and agreeing in direction is a weaker claim "
        "than agreeing in size', "
        "'directions agree beyond chance; magnitudes may still differ') AS verdict "
        "FROM (SELECT *, "
        f"least(1.0, 2 * arraySum(arraySlice({PASCAL}[comparable + 1], agreeing + 1)) "
        "/ pow(2, comparable)) AS sign_test_p FROM ("
        "SELECT feature, groupArray(film) AS films, groupArray(vs_residual) AS residuals, "
        "uniqExact(sign(vs_residual)) = 1 AS same_direction, "
        "count() OVER () AS comparable, "
        "countIf(uniqExact(sign(vs_residual)) = 1) OVER () AS agreeing "
        "FROM (" + " UNION ALL ".join(parts) + ") "
        "WHERE vs_residual IS NOT NULL AND vs_residual != 0 "
        f"GROUP BY feature HAVING count() = {len(films)})) ORDER BY feature"
    )
    return await MCP.run_query(sql)


async def negative_control(film: str) -> str:
    """The same correlation table, computed against SHUFFLED attention.

    Each feature is paired with another segment's attention instead of its own,
    so any correlation it returns was produced by the method rather than found by
    it. What it returns is not knowable without running it: run it and read the
    result. Never describe its outcome from memory of what a control is for.

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


# The demo-video corpus is a different question from the film corpus, and the
# allow-list is what keeps a metric name from reaching the SQL. Values are the
# column names in craft_videos; the labels are what a person would call them.
CRAFT_METRICS = {
    "duration_s": "runtime in seconds",
    "wpm": "words per minute",
    "words_total": "total words spoken",
    "sentence_words": "average words per sentence",
    "words_first_5s": "words in the first 5 seconds",
    "words_first_10s": "words in the first 10 seconds",
    "words_first_15s": "words in the first 15 seconds",
    "words_first_20s": "words in the first 20 seconds",
    "demo_verb_at_s": "seconds before the demo starts",
    "tail_wpm": "words per minute in the closing 10 seconds",
}


def _metric(value):
    key = str(value).strip().lower().replace(" ", "_")
    if key not in CRAFT_METRICS:
        raise ValueError(
            "unknown metric '%s' - the corpus holds: %s"
            % (value, ", ".join(sorted(CRAFT_METRICS)))
        )
    return key


async def craft_percentile(metric: str, value: float) -> str:
    """Where one number about a demo video sits among 73 real ones.

    A second corpus, unrelated to the films: 73 hackathon demo videos collected
    from Devpost, 56 with usable captions. Use it when the question is about how
    a video is made - how long, how fast, when the demo starts - and NOT when the
    question is about a film's audience.

    This returns a position and a spread. It cannot return a pass or a fail, and
    you must not describe it as one.

    Args:
        metric: one of duration_s, wpm, words_total, sentence_words,
            words_first_5s, words_first_10s, words_first_15s, words_first_20s,
            demo_verb_at_s, tail_wpm.
        value: the number to place in the distribution.
    """
    col = _metric(metric)
    v = float(value)
    sql = (
        f"SELECT '{CRAFT_METRICS[col]}' AS measures, "
        f"{v} AS your_value, "
        "count() AS corpus_n, "
        "round(median(m), 1) AS corpus_median, "
        "round(quantile(0.25)(m), 1) AS p25, "
        "round(quantile(0.75)(m), 1) AS p75, "
        "round(min(m), 1) AS lowest, round(max(m), 1) AS highest, "
        f"round(100 * countIf(m < {v}) / count()) AS your_percentile, "
        # The spread is returned next to the median because the median alone
        # reads as a target. Only 32% of this corpus falls in the 2-3 minute
        # band that its own median sits in, and a caller who sees "median 175"
        # without seeing "8 to 539" will treat 175 as the number to hit.
        "multiIf("
        f"  round(100 * countIf(m < {v}) / count()) < 10, 'in the lowest tenth of the corpus', "
        f"  round(100 * countIf(m < {v}) / count()) < 25, 'below the middle half', "
        f"  round(100 * countIf(m < {v}) / count()) <= 75, 'inside the middle half', "
        f"  round(100 * countIf(m < {v}) / count()) < 90, 'above the middle half', "
        "  'in the highest tenth of the corpus') AS position, "
        "'descriptive only - these are medians of what people did, not a "
        "threshold, and nothing here says a value outside the middle half "
        "performs worse' AS status "
        f"FROM (SELECT {col} AS m FROM craft_videos WHERE {col} IS NOT NULL)"
    )
    return await MCP.run_query(sql)


INSTRUCTION = """You answer questions about why an audience's attention rises and
falls across a film, using a database of measured segments and playback events.

Ground every claim in a tool result. If you did not query it, do not say it.

That includes checks. Never report that a control, a comparison, or a second film
confirms anything unless you called the tool and read what came back. Saying "the
negative control confirms this" without having run it is the exact failure this
system exists to catch, and doing it here would discredit every true sentence
around it. If a check is worth citing, run it.

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

Where a row already states a direction or a verdict, quote that word. Do not
re-derive it from the numbers beside it: a row that says "positive on every film"
is not open to interpretation, and re-deriving is where transcription errors come
from.

A question about whether a result replicates, generalises, or holds up across
films is answered with direction_agreement, never by comparing two correlation
tables by eye. Reporting that films "agree" or "disagree" without that tool is
reading a pattern out of six numbers, which is the thing this system is for
preventing.

craft_percentile reads a DIFFERENT corpus - 73 demo videos, nothing to do with
the films - and its numbers are descriptive, not prescriptive. Report the
percentile and the range together, always. Never say a value is too long, too
fast, wrong, or needs fixing: the corpus shows no cost to sitting outside the
middle half, and it contains no outcome to compare against. "Longer than 8 in 10
of them" is a fact; "too long" is not one this table can support.

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


def build_agent(model=None, film=None):
    """The agent, built fresh per question but from one definition.

    The CLI and the web server both come through here, so there is no second
    copy of the tool list or the instruction to drift out of sync.
    """
    corpus = ", ".join(sorted(LOADED)) or "nothing yet"
    return LlmAgent(
        name="second_pass",
        model=model or MODEL,
        description="Answers questions about attention and content across a film corpus.",
        instruction=INSTRUCTION
        + f"{NEWLINE}{NEWLINE}The corpus holds exactly one film id per entry, and right "
        f"now that is: {corpus}. Never query any other."
        # Without this the model picked a film at random for any question that did
        # not name one. On camera that means selecting Tears of Steel, asking which
        # features explain attention, and being told about Big Buck Bunny.
        + (f"{NEWLINE}{NEWLINE}The person asking is looking at '{film}' right now. A "
           f"question that does not name a film is about '{film}'. Only query a "
           "different film when the question names it, or when the question is about "
           "the corpus as a whole." if film else ""),
        tools=[
            underperforming_stretches,
            content_at,
            correlation_table,
            negative_control,
            direction_agreement,
            craft_percentile,
            exit_hotspots,
        ],
    )


async def stream(question, film=None):
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
    async for item in _run_with_retry(question, film):
        yield item


TRANSIENT = ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "500", "INTERNAL")

# (model index, seconds to wait first). The primary gets a second chance before
# the chain moves on, because most 503s are momentary; after that, waiting longer
# on a congested model is worse than asking a different one.
LADDER = ((0, 0), (0, 3), (1, 1), (2, 1))


async def _run_with_retry(question, film=None):
    """Run the turn, retrying the transient upstream failures.

    Gemini returns 503 "experiencing high demand" often enough to hit a recording,
    and ADK propagates it straight out of run_async. Before this, the page showed
    a raw Python exception string where the answer goes — during a demo whose
    whole claim is that it is unedited.

    Retrying is only safe before anything has been yielded: once tool rows are on
    screen, a second attempt would duplicate them. That is also the case that
    matters, because these failures land on the first model call. A failure after
    events have been emitted is surfaced instead.
    """
    last = len(LADDER) - 1
    for step, (index, wait) in enumerate(LADDER):
        model = MODELS[min(index, len(MODELS) - 1)]
        if wait:
            await asyncio.sleep(wait)

        emitted = False
        try:
            async for item in _run_once(question, model, film):
                if not emitted:
                    emitted = True
                    yield ("model", model, None)
                yield item
            return
        except Exception as exc:  # noqa: BLE001 - classified below
            text = str(exc)
            transient = any(marker in text for marker in TRANSIENT)
            if emitted or not transient or step == last:
                raise
            nxt = MODELS[min(LADDER[step + 1][0], len(MODELS) - 1)]
            action = "retrying" if nxt == model else f"falling back to {nxt}"
            print(f"[{model} unavailable, {action}] {text[:100]}", flush=True)


async def _run_once(question, model, film=None):
    runner = InMemoryRunner(agent=build_agent(model, film), app_name="second-pass")
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
            if kind == "model":
                if a != MODELS[0]:
                    print(f"[{MODELS[0]} unavailable — answered by {a}]")
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
