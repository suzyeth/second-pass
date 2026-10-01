# Second Pass

[![Reproduce analysis](https://github.com/suzyeth/second-pass/actions/workflows/reproduce.yml/badge.svg)](https://github.com/suzyeth/second-pass/actions/workflows/reproduce.yml)

Every attention-analytics product promises to tell you which stretches lose your
audience, and why. We built the machinery to test that claim, pointed it at our own
hypothesis first, and it did not survive.

What is left is the machinery, and it turned out to be the better product: three films'
measured attention and Gemini's blind shot-level scores in one queryable place, with an
agent that reports the strength of what it finds — including when that strength is
nothing.

It has now caught us three times. Scoring the third film destroyed the last pattern the
first two had. Correcting the tie handling in the rank correlation moved the headline
finding across the significance floor. And a fifth of every upload turned out to be end
credits, scored as film — which is where "second-half attention runs 4.40x the first
half" came from, and where the only correlation that ever cleared the floor came from.
The page says all three.

**[Live demo](https://second-pass-334984245629.us-central1.run.app)** · **[3-minute video](https://youtu.be/0TThYr3WP3s)** · [How it
works](#how-it-works), including the negative control — the part that makes the rest
worth trusting.

Built for **Agentic Cinema**, ClickHouse track. Gemini via Google ADK; every answer is a
ClickHouse query executed by the official `mcp-clickhouse` MCP server.

---

## The finding this is built around

The obvious product here is "we predict which stretches lose your audience." That
claim was tested on day one and has not survived any test since. What it has done
instead is expose three of our own errors, each caught by the machinery rather than
by us noticing.

### The third one first, because it moved every number on this page

The 100-bin grid covers the whole YouTube upload, and **the upload ends with
credits**. Frame sampling pins the boundary at 588s on Tears of Steel, 489.5s on Big
Buck Bunny, 744s on Sintel — roughly a fifth of each upload, scored as film and
correlated against attention.

On Tears of Steel the film averages 0.074 attention and the credits average
**0.591**, and the upload's maximum sits at bin 97 — a post-credits scene people
jump to. So "second-half attention runs 4.40x the first half," the number this
project was built around, was mostly the credits. **On the film itself it is 1.28x.**

Filtering those rows out of the correlation is not enough, and our first attempt did
exactly that. `attention_base` is a moving average, so the window for the last
stretch of *film* reaches into a region eight times higher and every residual near
the end is an artifact of data that is not film. `prepare.py` truncates the curve
**before** detrending.

| | Tears of Steel | Big Buck Bunny | Sintel |
| --- | --- | --- | --- |
| film segments | 80 | 82 | 84 |
| end credits | 20 | 18 | 16 |
| usable after edges | **64** | **66** | **68** |
| position bias, film only | **1.28x** | **1.55x** | **1.59x** |
| position bias, whole upload | 4.40x | 3.00x | 1.91x |

### The correlations

A correlation has to clear the Bonferroni floor for six features at its film's n —
**0.332, 0.327, 0.322**. Judging six results against the one-test floor (0.247,
0.243, 0.239) is how a null gets reported as a finding, so both are columns.

| feature | Tears of Steel | Big Buck Bunny | Sintel |
| --- | --- | --- | --- |
| | *raw / residual* | *raw / residual* | *raw / residual* |
| visual event density | +0.400 / +0.239 | +0.429 / +0.174 | +0.269 / +0.138 |
| story information | +0.321 / +0.199 | −0.015 / −0.037 | +0.309 / **+0.343** |
| character presence | −0.040 / −0.093 | −0.094 / −0.116 | +0.225 / +0.309 |
| score intensity | +0.432 / +0.290 | +0.258 / +0.213 | +0.239 / +0.022 |
| speech density | +0.215 / +0.122 | n/a — no dialogue | +0.130 / +0.276 |
| inertness | −0.291 / −0.087 | −0.020 / +0.035 | −0.249 / −0.171 |

Across three films and eighteen tests, **one residual clears its corrected floor**:
story information on Sintel, at +0.343 against 0.322. The agent's own verdict column
calls it *"marginal — clears the floor by too little to believe from one film"*, and
it is right twice over:

- **It does not replicate.** The same feature reads +0.199 on Tears of Steel and
  −0.037 on Big Buck Bunny.
- **It rests on very few segments.** Moving the credits boundary by one bin drops it
  back below the floor. That shift is not a real alternative — frame sampling pins
  the boundary to within two seconds — but it measures how thin the result is, and
  `analyze-credits.py` reports boundary *uncertainty* and *fragility* as two separate
  lines for exactly this reason.

The result that used to sit here was score intensity on Big Buck Bunny at +0.317.
**That one was the credits**, and it is now +0.213 against a floor of 0.327.

### Was the null just the segmentation?

A bin is 7.34s on Tears of Steel and the median shot is 3.2s, so 69 of 100 bins
straddle at least one cut and the average bin holds 2.4 shot fragments. Every content
score is an average over heterogeneous material, and averaging heterogeneous material
attenuates any correlation drawn from it. That is a third reading of the null, next to
"content does not predict attention" and "the scores are too noisy" — and it is the
only one of the three that is cheap to test.

So the shots were detected with `ffmpeg` (threshold 0.3, chosen from a plateau: 0.2
through 0.35 finds 131/125/122/119 cuts, and 0.45 falls off a cliff to 84), merged into
82 beats across the three films whose boundaries are always real cuts, scored through
the **same prompt, model, temperature and resolution** as the bins, and then each bin
inherited the score of the beat its midpoint falls in. Same residuals, same rows, same
floors — the only thing that changed is that x was measured on coherent material.

**Nothing moved.** Across 17 comparable tests, 8 correlations got stronger and 9 got
weaker; sign test p = 1.000, median change −0.008. The hypothesis predicted a
systematic rise. There was no systematic anything.

Two honest limits on that. The beat side carries only 21–25 *independent* content
measurements spread over 64–68 rows, so its own floor is 0.539–0.590 rather than
0.322–0.332 and it could never have produced a significant result on its own — which
is why the test is the paired comparison, not either column. And neither scoring pass
has a reliability estimate, so a difference here could still have been resampling
noise. What can be said is narrow and real: **the prediction that segmentation was
hiding a signal was tested and it failed.**

`analyze-beats.py` reproduces this; `films/BEATS-ANALYSIS.txt` is the output.

### What the third film did

With two films, all five comparable features pointed the same direction — a
two-tailed sign test at p = 0.063, suggestive and not significant, and the README
said a third film would settle it.

**That p = 0.063 was credits too.** Recomputed on the film regions with the baseline
rebuilt, those same two films agree on **3 of 5**, p = 1.000. The result that
justified scoring a third film did not exist either.

The third film was scored anyway, and across all three agreement is **2 of 5**, sign
test **p = 1.000**: the directions are as consistent as coin flips. Story
information, character presence and inertness all disagree.

**The two-film consistency was two films' worth of noise.** That is the finding, and
it is the one the machinery was built to be able to produce. A system that could only
ever confirm would have reported the p = 0.063 and stopped — and would have reported
4.40x, and +0.317, and never looked at what was actually in those segments.

So the product is not prediction. It is the two datasets in one place, queryable in a
conversation, reporting the strength of what it finds including when that strength is
nothing.

---

## How it works

```
ingest    yt-dlp -> YouTube most-replayed heatmap (100 bins, always)
          film from download.blender.org (CC-BY)
prepare   locate the credits by frame sampling, truncate the curve there,
          THEN detrend against a +-8-bin moving average -> residual
score     each segment at 360p/2fps with audio, inline to Gemini, blind prompt:
          it never sees an attention value and is told not to guess preferences
store     ClickHouse. 3 films, 300 segments (246 film, 54 credits), 198 usable
          after edges, 13.3M synthetic playback events
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
across sixty-odd segments, ties are most of the data. The two methods disagree by up
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
| score intensity (raw) | +0.432 | −0.090 |
| visual event density (raw) | +0.400 | −0.145 |
| story information (residual) | +0.199 | −0.110 |

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

## The second corpus, and why it is here

The same machinery, pointed at a completely different question, to show that the
discipline is not specific to film.

`craft_videos` holds **73 real hackathon demo videos** collected from Devpost, 56
with usable English captions: runtime, words per minute, how many words land in the
first fifteen seconds, when the first demo verb appears, whether the close speeds
up. `craft_percentile` places a number in that distribution.

**It cannot return a pass or a fail, and the tool is built so that it cannot.** Every
answer carries the percentile, the median, the interquartile range *and* the full
span, plus a column that says in words that this is descriptive. Ask it about a
three-minute cut and it says 60th percentile, median 175 seconds, range 8 seconds to
9 minutes — and refuses to call anything too long.

That refusal is the point. The corpus was sampled in two batches, winners and
non-winners of the same events, and the difference between them is weak and
confounded: median duration 174s against 175s, and the winners' faster speech tracks
their being more often teams (44% vs 23% multi-voice) rather than anything about
pace. So the batches are pooled and `batch` survives only as provenance. A tool that
turned these medians into thresholds would be doing exactly what this project exists
to catch — dressing a correlation as a rule.

The one thing that *may* fail a build is a competition's own written duration cap.
A rule is a rule; a median is not.

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
- Three films, 64 / 66 / 68 usable segments. Per-film n is structurally capped near
  this: the heatmap is always 100 bins whatever the runtime, a fifth of each upload
  is credits, and 16 more go to the edge window. Single-film significance at these
  effect sizes is out of reach. Three films reach p = 1.000 on direction; a fourth
  would not rescue that, it would test it again.
- **The credits boundary is measured, not exact.** Frame sampling pins it to within
  two seconds on each film, and no verdict moves inside that window. Shifting it a
  full bin does move one — Sintel's story information, the only result that clears
  anything. That is not an alternative analysis, but it is a fair measure of how
  little the result rests on, and `analyze-credits.py` prints both lines rather than
  choosing the flattering one.
- **Tears of Steel has a post-credits scene**, roughly 711–734s, which is film and is
  excluded anyway: three bins cut off from the body by 123 seconds of credits have no
  valid local baseline. It is also where the upload's highest attention sits.
- All three are Blender open movies scored by the same prompt, so they are not
  independent in the way three unrelated productions would be. That makes the
  disagreement between them more striking, not less.
- Bins near either end have a one-sided moving-average baseline, so their residual is
  an artifact of the window. They are kept in the table, drawn differently in the UI,
  and excluded from every claim.
- **The detrend window is ±8 bins, which is a different amount of time on each
  film**: 58.7s on Tears of Steel, 47.8s on Big Buck Bunny, 71.0s on Sintel, because
  the heatmap is always 100 bins and the runtimes differ. Cross-film agreement
  therefore compares residuals that were high-pass filtered at three different
  timescales. Redoing it on a fixed ±60s window moves the one result that clears
  anything — Sintel's story information reads +0.286 against a floor of 0.318, and
  no longer clears. It still clears at ±45s and ±75s.
  The ±8-bin choice was made in the original design, before any of these numbers
  existed, so it stays the primary analysis and the others are reported as what they
  are: a sensitivity check showing the result moves with an arbitrary choice. Picking
  whichever window makes it significant would be the same error as judging six
  features against a one-test floor, one level up.
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
