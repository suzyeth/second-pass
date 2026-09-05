# Second Pass

Every attention-analytics product promises to tell you which stretches lose your
audience, and why. We built the machinery to test that claim, pointed it at our own
hypothesis first, and it did not survive.

What is left is the machinery, and it turned out to be the better product: three films'
measured attention and Gemini's blind shot-level scores in one queryable place, with an
agent that reports the strength of what it finds — including when that strength is
nothing. Scoring the third film destroyed the last pattern the first two had; the page
says so.

**[Live demo](https://second-pass-334984245629.us-central1.run.app)** · **[3-minute video](<YOUTUBE URL>)** · [How it
works](#how-it-works), including the negative control — the part that makes the rest
worth trusting.

Built for **Agentic Cinema**, ClickHouse track. Gemini via Google ADK; every answer is a
ClickHouse query executed by the official `mcp-clickhouse` MCP server.

---

## The finding this is built around

The obvious product here is "we predict which stretches lose your audience." That
claim was tested on day one, and then tested twice more as the corpus grew. It does
not survive any of them.

Attention rises toward the end of most films regardless of what is on screen. Second-half
attention runs **4.40×** the first half on Tears of Steel, **3.00×** on Big Buck Bunny,
**1.91×** on Sintel. Correlate a content feature against raw attention and you are
mostly measuring position in the runtime.

A correlation has to clear **0.290** to stand out from noise here — the single-test
floor of 0.215 at n=84, corrected for testing all six features at once. Judging six
results against a one-test floor is how a null gets reported as a finding.

| feature | Tears of Steel | Big Buck Bunny | Sintel |
| --- | --- | --- | --- |
| | *raw / residual* | *raw / residual* | *raw / residual* |
| character presence | **−0.382** / −0.154 | −0.106 / −0.096 | −0.063 / +0.189 |
| score intensity | **+0.354** / +0.144 | **+0.315** / **+0.317** | +0.171 / +0.032 |
| story information | −0.188 / −0.028 | **−0.333** / −0.059 | +0.015 / +0.215 |
| inertness | +0.205 / +0.048 | +0.256 / +0.039 | +0.000 / −0.114 |
| visual event density | +0.172 / +0.213 | −0.004 / +0.243 | +0.034 / +0.093 |
| speech density | −0.096 / +0.082 | n/a — no dialogue | +0.074 / +0.212 |

Across three films and eighteen tests, **exactly one residual correlation clears the
corrected floor**: score intensity on Big Buck Bunny, at +0.317, beating 0.290 by 0.027.
On the other two films the same feature reads +0.144 and +0.032. It does not replicate.

Big Buck Bunny has no dialogue, so speech density there is a column of identical zeroes.
It has no correlation — undefined, not zero, and the difference matters: "no relationship
shown" would claim a measurement that was never possible.

### What the third film did

With two films, all five comparable features pointed the same direction. A two-tailed
sign test put that at **p = 0.063** — suggestive, not significant, and the README said so
while noting a third film would settle it.

The third film settled it. Agreement dropped to **2 of 5**, sign test **p = 1.000**:
the directions are now as consistent as coin flips. Story information, character presence
and inertness all flip sign on Sintel.

**The two-film consistency was two films' worth of noise.** That is the finding, and it
is the one the machinery was built to be able to produce. A system that could only ever
confirm would have reported the p = 0.063 and stopped.

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
store     ClickHouse. 3 films, 300 segments, 13.3M synthetic playback events
agent     Google ADK -> mcp-clickhouse (MCP) -> ClickHouse Cloud
ui        player, attention curve, residual, proof panel
```

**The model does not write SQL.** It picks one of six parameterised questions and
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

**Cross-film agreement is a query, not an eyeball.** `direction_agreement` puts every
feature's residual side by side across films and computes the two-tailed sign test in
SQL, with the direction, the p-value and the verdict as columns. Asked whether the films
agreed before this existed, the model compared two tables by eye, called five agreeing
features a match, and filed a feature with two positive residuals under negative. The
page reads the same endpoint rather than recomputing: two implementations of one
statistic is how a page and an agent end up telling a visitor different things about the
same corpus.

---

## The three data sources, and which is which

| | what | status |
| --- | --- | --- |
| attention | YouTube most-replayed heatmap | **measured**, and it records rewatching, not retention |
| content | six scores per segment from Gemini | **measured**, blind to attention |
| playback events | 13.3M session events | **synthetic**, generated from the attention curve |

The events are synthetic because per-viewer playback data is something only a platform
owner has. They are derived *from* the attention curve, so they demonstrate the query
workload and cannot corroborate the curve. Every claim about the film comes from the
`segments` table. `load.py` verifies the generator by aggregating its output back into
the curve it came from (rho = +0.849, +0.739 and +0.500) rather than asserting it.

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
- Three films, 84 usable segments each. Per-film n is structurally capped near this,
  because the heatmap is always 100 bins whatever the runtime, so single-film
  significance at these effect sizes is out of reach. Three films reach p = 1.000 on
  direction; a fourth would not rescue that, it would test it again.
- All three are Blender open movies scored by the same prompt, so they are not
  independent in the way three unrelated productions would be. That makes the
  disagreement between them more striking, not less.
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

The code is MIT.

The three films — **Tears of Steel**, **Big Buck Bunny** and **Sintel** — are
© copyright Blender Foundation, licensed **CC-BY 3.0**
(<https://creativecommons.org/licenses/by/3.0/>). They are used here under that
licence, which is why the corpus is these three and not whatever had the best
heatmap: everything downstream — the clips, the scores, the frames in the demo
video — is a derivative work, and only an open licence makes publishing it
possible. The attention data is YouTube's public most-replayed heatmap for each
upload, read with `yt-dlp`.
