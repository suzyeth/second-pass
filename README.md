# Second Pass

Audience attention and shot-level content analysis in one queryable place — and an
honest report of how weakly they actually relate.

Built for **Agentic Cinema**, ClickHouse track. Gemini via Google ADK, every answer
grounded in a ClickHouse query executed by the official `mcp-clickhouse` MCP server.

---

## The finding this is built around

The obvious product here is "we predict which stretches lose your audience." That
claim was tested on day one and it does not survive.

Attention rises toward the end of almost every film regardless of what is on screen.
On Tears of Steel, second-half attention runs **4.40×** the first half; on Big Buck
Bunny, **3.00×**. Correlate any content feature against raw attention and you are
mostly measuring position.

A correlation has to clear **0.290** to stand out from noise here — the single-test
floor of 0.215 at n=84, corrected for testing all six features at once. Judging six
results against a one-test floor is how a null gets reported as a finding.

### Tears of Steel, 84 non-edge segments

| feature | vs measured | vs residual | vs position | |
| --- | --- | --- | --- | --- |
| character presence | **−0.382** | −0.154 | −0.270 | position artifact |
| score intensity | **+0.354** | +0.144 | **+0.365** | position artifact |
| inertness | +0.205 | +0.048 | +0.167 | nothing shown |
| story information | −0.188 | −0.028 | −0.269 | nothing shown |
| visual event density | +0.172 | +0.213 | +0.178 | nothing shown |
| speech density | −0.096 | +0.082 | −0.159 | nothing shown |

Two features clear the floor against measured attention. **Neither survives correcting
for position, and nothing takes their place** — the largest residual correlation is
+0.213, below even the uncorrected floor.

### Big Buck Bunny, 84 non-edge segments

| feature | vs measured | vs residual | vs position | |
| --- | --- | --- | --- | --- |
| story information | **−0.333** | −0.059 | **−0.343** | position artifact |
| score intensity | **+0.315** | **+0.317** | +0.176 | marginal |
| inertness | +0.256 | +0.039 | **+0.299** | nothing shown |
| character presence | −0.106 | −0.096 | −0.091 | nothing shown |
| visual event density | −0.004 | +0.243 | −0.157 | nothing shown |
| speech density | n/a | n/a | n/a | **undefined** |

The film has no dialogue, so speech density is a column of identical zeroes. It has no
correlation — undefined, not zero, and the difference matters: "no relationship shown"
would claim a measurement that was never possible.

One feature clears the residual floor here, score intensity at +0.317. It beats 0.290
by 0.027, which is marginal and not something to believe from one film.

### Across both

Five of five comparable features point the same direction on both films. Under a
two-tailed sign test that is **p = 0.063** — suggestive, not significant. And direction
is the weak version of the claim: score intensity's residual is +0.144 on one film and
+0.317 on the other, so the magnitudes disagree by more than a factor of two.

Per-film n is capped near 84 by construction, because the heatmap is always exactly 100
buckets whatever the runtime. Single-film significance at these effect sizes is out of
reach. Cross-film direction consistency is the only statistical story left, and with two
films it cannot reach significance either. A third film would settle it.

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
store     ClickHouse. 2 films, 200 segments, 8.3M synthetic playback events
agent     Google ADK -> mcp-clickhouse (MCP) -> ClickHouse Cloud
ui        player, attention curve, residual, proof panel
```

**The model does not write SQL.** It picks one of five parameterised questions and
fills in its arguments; the SQL is composed in `agent.py` from a template and executed
through MCP's `run_query`. That keeps MCP genuinely on the runtime path — the track's
requirement — while keeping the statements deterministic, which matters when the demo
is unedited. Every parameter is validated against an allow-list read from the `films`
table at startup.

**Correlations are computed with tie-averaged ranks.** ClickHouse's `rankCorr` does
not average tied ranks, and with content scores taking seven to nine distinct values
across eighty-four segments, ties are most of the data. The two methods disagree by up
to 0.083 — enough to move a feature across the significance floor and back. The SQL
builds average ranks with window functions and correlates those, which reproduces
`analyze-film.py` to three decimals on every field.

**Judgements are computed in SQL, not asked for in prose.** `correlation_table` returns
both noise floors, a `verdict`, and a `position_artifact`
flag as columns. Asked in the prompt to call a hair over the floor "marginal", the
model reported it as a finding instead. A column cannot be talked around.

**The method is tested against itself.** `negative_control` runs the identical query
with each feature paired against a *different* segment's attention — segment `r` against
segment `(37r + 11) mod n`, a real permutation because 37 is coprime with 84, and a
fixed one because a control that moves between runs is not a control. Everything
collapses:

| feature (Tears of Steel) | real | shuffled |
| --- | --- | --- |
| character presence | −0.382 | −0.121 |
| score intensity | +0.354 | +0.099 |
| visual event density (residual) | +0.213 | −0.062 |

Nothing clears the floor. A method that still found structure there would be
manufacturing it, and no result from it could be trusted. The proof panel runs this
live, on a toggle.

---

## The three data sources, and which is which

| | what | status |
| --- | --- | --- |
| attention | YouTube most-replayed heatmap | **measured**, and it records rewatching, not retention |
| content | six scores per segment from Gemini | **measured**, blind to attention |
| playback events | 8.3M session events | **synthetic**, generated from the attention curve |

The events are synthetic because per-viewer playback data is something only a platform
owner has. They are derived *from* the attention curve, so they demonstrate the query
workload and cannot corroborate the curve. Every claim about the film comes from the
`segments` table. `load.py` verifies the generator by aggregating its output back into
the curve it came from (rho = +0.849 and +0.739) rather than asserting it.

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
- Two films, 84 usable segments each. Per-film n is structurally capped near this,
  because the heatmap is always 100 bins whatever the runtime, so single-film
  significance at these effect sizes is out of reach. Two films is also the smallest
  number that can agree about anything, and 5-of-5 direction agreement is p = 0.063.
  A third film is the next thing this needs, ahead of any feature.
- Both films are Blender open movies scored by the same prompt, so they are not
  independent in the way two unrelated productions would be.
- Bins near either end have a one-sided moving-average baseline, so their residual is
  an artifact of the window. They are kept in the table, drawn differently in the UI,
  and excluded from every claim.
- The ±8-bin detrend window caps what is findable at all. A content effect lasting
  longer than about two minutes is absorbed into its own baseline, so "nothing in the
  residual" means nothing *on a short timescale*. Residualising against one monotone
  position curve fitted across films would let long stretches count; it is not built.
- `attention_raw` is min-max normalised per film by YouTube, so it is not comparable
  between films. Only residuals are even arguably poolable.
- The Gemini content scores have no reliability estimate. Temperature is 0, but nothing
  here measures test-retest agreement, and noisy scores attenuate every correlation —
  some of this null could be measurement noise rather than absence.

## Licence

MIT. Tears of Steel and Big Buck Bunny are © Blender Foundation, CC-BY 3.0.
