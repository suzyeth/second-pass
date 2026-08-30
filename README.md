# Second Pass

Audience attention and shot-level content analysis in one queryable place — and an
honest report of how weakly they actually relate.

Built for **Agentic Cinema**, ClickHouse track. Gemini via Google ADK, every answer
grounded in a ClickHouse query executed by the official `mcp-clickhouse` MCP server.

---

## The finding this is built around

The obvious product here is "we predict which stretches lose your audience." That
claim was tested on day one, against Tears of Steel, and it does not survive.

Attention rises toward the end of almost every film regardless of what is on screen.
On this film, second-half attention runs **3.39×** the first half. Correlate any
content feature against raw attention and you are mostly measuring position.

Across the 84 non-edge segments:

| feature | vs measured attention | vs residual | vs position | |
| --- | --- | --- | --- | --- |
| score intensity | **+0.366** | +0.161 | **+0.377** | position artifact |
| character presence | **−0.346** | −0.124 | **−0.237** | position artifact |
| inertness | **+0.224** | +0.071 | +0.187 | position artifact |
| visual event density | +0.183 | **+0.224** | +0.189 | marginal |
| story information | −0.164 | −0.007 | **−0.244** | nothing shown |
| speech density | +0.004 | +0.165 | −0.053 | nothing shown |

A rank correlation has to clear **0.215** at this sample size to be distinguishable
from noise. **Three features clear it against measured attention. None of the three
survives correcting for position.** One feature survives — by 0.009, which is a coin
landing on its edge, not a finding.

"Louder score means more watched" is significant, is the kind of result a deck gets
built on, and is entirely an artifact of where those segments sit in the film.

So the product is not prediction. It is the two datasets in one place, queryable in a
conversation, reporting the strength of what it finds including when that strength is
nothing. The falsification machinery was always the differentiator; the day-one result
made it the product.

---

## How it works

```
ingest    yt-dlp -> YouTube most-replayed heatmap (100 bins, always)
          film from download.blender.org (CC-BY)
prepare   bins + detrend against a +-8-bin moving average -> residual
score     each segment at 360p/2fps with audio, inline to Gemini, blind prompt:
          it never sees an attention value and is told not to guess preferences
store     ClickHouse. 95 segments, 4.2M synthetic playback events
agent     Google ADK -> mcp-clickhouse (MCP) -> ClickHouse Cloud
ui        player, attention curve, residual, proof panel
```

**The model does not write SQL.** It picks one of four parameterised questions and
fills in its arguments; the SQL is composed in `agent.py` from a template and executed
through MCP's `run_query`. That keeps MCP genuinely on the runtime path — the track's
requirement — while keeping the statements deterministic, which matters when the demo
is unedited. Every parameter is validated against an allow-list read from the `films`
table at startup.

**Judgements are computed in SQL, not asked for in prose.** `correlation_table` returns
`significant_above` (the noise floor at this n), a `verdict`, and a `position_artifact`
flag as columns. Asked in the prompt to call 0.224 against a 0.215 floor "marginal",
the model reported it as a finding instead. A column cannot be talked around.

**The method is tested against itself.** `negative_control` runs the identical query
with each feature paired against a *different* segment's attention — segment `r` against
segment `(37r + 11) mod n`, a real permutation because 37 is coprime with 84, and a
fixed one because a control that moves between runs is not a control. Everything
collapses:

| feature | real | shuffled |
| --- | --- | --- |
| score intensity | +0.366 | +0.117 |
| character presence | −0.346 | −0.092 |
| visual event density (residual) | +0.224 | −0.047 |

Nothing clears the floor. A method that still found structure there would be
manufacturing it, and no result from it could be trusted. The proof panel runs this
live, on a toggle.

---

## The three data sources, and which is which

| | what | status |
| --- | --- | --- |
| attention | YouTube most-replayed heatmap | **measured**, and it records rewatching, not retention |
| content | six scores per segment from Gemini | **measured**, blind to attention |
| playback events | 4.2M session events | **synthetic**, generated from the attention curve |

The events are synthetic because per-viewer playback data is something only a platform
owner has. They are derived *from* the attention curve, so they demonstrate the query
workload and cannot corroborate the curve. Every claim about the film comes from the
`segments` table. `load.py` verifies the generator by aggregating its output back into
the curve it came from (rho = +0.841) rather than asserting it.

---

## Running it

Needs `CLICKHOUSE_HOST`, `CLICKHOUSE_PASSWORD`, and `GOOGLE_API_KEY` in the
environment. On Windows, `run.ps1` loads the first two from the user registry and the
third from Secret Manager, and echoes none of them.

```bash
pip install -r requirements.txt

python server.py                                  # the web UI on :8080
python agent.py "which stretches lose people?"    # the same agent, in a terminal
python mcp_probe.py                               # prove MCP reaches the tables
python runq.py                                    # the canonical queries, timed
python analyze.py                                 # reproduce the statistics
```

To rebuild the corpus from scratch: `prepare.py` (fetch, bin, detrend, cut),
`score-film.js` (Gemini, resumable — the free-tier video quota dies around 20 calls,
so it writes after every clip and skips what is already scored), then `load.py`.

---

## Deployment

Cloud Run, from the `Dockerfile`. The image carries two Python programs: the server,
and the `mcp-clickhouse` MCP server it spawns as a subprocess and talks to over stdio.
Credentials come from Secret Manager; nothing is baked into the image.

---

## Known limits, stated rather than hidden

- The heatmap is not in the YouTube Data API. It is scraped from an undocumented
  endpoint and cached. A channel owner's own `audienceWatchRatio` is strictly better
  and is what a production version would use.
- It measures **rewatch, not exit**. A peak means people went back, not that they
  stayed.
- One film, 84 usable segments. The one marginal result would need a second film
  before it was worth believing, and the README will say so until it has one.
- Bins near either end have a one-sided moving-average baseline, so their residual is
  an artifact of the window. They are kept in the table, drawn differently in the UI,
  and excluded from every claim.

## Licence

MIT. Tears of Steel and Big Buck Bunny are © Blender Foundation, CC-BY 3.0.
