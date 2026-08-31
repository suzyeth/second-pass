# Second Pass — attention and content, in one queryable place

Target: **Agentic Cinema**, ClickHouse track. Deadline **2026-09-09 14:00 PDT**
(verified against the Devpost page 2026-08-31; an earlier draft of this document
said 09-07, which was wrong by two days).
Written 2026-08-27, after the day-1 gate was run and did not pass.
Supersedes the 2026-08-08 concept, which is kept outside this repository.

---

## What changed, and why the project survives it

The original design bet on one claim: *content explains attention*. Find the stretches
an audience abandons, look at what is on screen there, and you can say why — and then
fix it. Everything downstream assumed that claim held.

**It was tested on day 1 and it does not hold at any strength worth building on.**

That is not a footnote. It is the most important thing this project knows, and it is
now the thing it is built to report rather than the thing it is built to assume.

### The measurement

Tears of Steel (734s, 2.66M views). YouTube's most-replayed heatmap gives exactly 100
bins — always 100, regardless of runtime — so a bin is 7.34s.

Raw attention is dominated by **position**, not content:

| bins | at | mean attention |
| --- | --- | --- |
| 0–9 | 0s | 0.130 |
| 10–19 | 74s | 0.033 |
| 40–49 | 294s | 0.072 |
| 70–79 | 514s | 0.122 |
| 80–89 | 588s | **0.454** |
| 90–99 | 662s | **0.766** |

The last fifth carries almost all the high values. Sampling the raw extremes would have
produced groups separated by *where in the film they are* (mean bin 22.9 vs 81.2) — and
any content feature that correlates with position would then look predictive. That is a
false GO, and it is the trap the 08-08 cold read predicted.

So attention was **detrended**: residual against a ±8-bin moving average, i.e. *more or
less watched than its position predicts*. The 12 lowest and 12 highest residuals are
position-matched (mean bin 53.6 vs 55.0).

Those 24 stretches were cut at 360p / 2fps **with audio**, and scored by Gemini on six
content dimensions with a **blind prompt** — it never saw an attention value and was
told explicitly not to guess whether viewers liked the clip.

### The result

The pilot scored 24 position-matched extremes and found all six fields pointing the same
way with the strongest |rho| at 0.316 — consistent direction, insufficient power. Both
films are now fully scored, and the full sample says something sharper.

A correlation has to clear **0.290** to stand out from noise at n=84: the single-test
floor of 0.215, corrected for testing six fields at once. An earlier version of this
document judged six results against the one-test floor, which is how a null gets
reported as a finding.

**Tears of Steel** (n=84, position bias 4.40×):

| field | vs raw | vs residual | vs position | verdict |
| --- | --- | --- | --- | --- |
| character_presence | **−0.382** | −0.154 | −0.270 | position artifact |
| score_intensity | **+0.354** | +0.144 | **+0.365** | position artifact |
| inertness | +0.205 | +0.048 | +0.167 | nothing shown |
| story_information | −0.188 | −0.028 | −0.269 | nothing shown |
| visual_event_density | +0.172 | +0.213 | +0.178 | nothing shown |
| speech_density | −0.096 | +0.082 | −0.159 | nothing shown |

Two fields clear the floor against raw attention and **neither survives the correction**.
Their correlation with position is as large as their correlation with attention, or
larger. "Louder score means more watched" is significant, is the kind of finding a deck
gets built on, and is entirely an artifact of where those segments sit in the film.
Nothing takes their place: the largest residual is +0.213, below even the uncorrected
floor.

**Big Buck Bunny** (n=84, position bias 3.00×):

| field | vs raw | vs residual | vs position | verdict |
| --- | --- | --- | --- | --- |
| story_information | **−0.333** | −0.059 | **−0.343** | position artifact |
| score_intensity | **+0.315** | **+0.317** | +0.176 | marginal |
| inertness | +0.256 | +0.039 | **+0.299** | nothing shown |
| character_presence | −0.106 | −0.096 | −0.091 | nothing shown |
| visual_event_density | −0.004 | +0.243 | −0.157 | nothing shown |
| speech_density | n/a | n/a | n/a | **undefined** |

The film has no dialogue. speech_density is a column of identical zeroes, so it has no
correlation at all — undefined, not zero. Before this was handled, ClickHouse returned
NaN, `abs(NaN) < floor` was false, the verdict fell through every branch to the last,
and the system reported that speech density *holds up after correcting for position* on
a film with no speech in it. A confident finding about a constant column is exactly the
failure this project exists to catch, produced by this project.

One field clears the residual floor here: score_intensity at +0.317, beating 0.290 by
0.027. Marginal, and not to be believed from one film.

**Across both:** five of five comparable fields point the same direction. Two-tailed
sign test, **p = 0.063** — suggestive, not significant. And direction is the weak form of
the claim; score_intensity's residual is +0.144 on one film and +0.317 on the other, so
the magnitudes disagree by more than two-fold. Per-film n is capped near 84 because the
heatmap is always exactly 100 buckets whatever the runtime, so single-film significance
at these effect sizes is unreachable by construction. Cross-film direction consistency is
the only statistical story left and two films cannot carry it. A third would settle it.

Both films are Blender open movies scored by the same prompt, so they are not
independent in the way two unrelated productions would be.

An earlier version of this table read 0.366 / 0.224 and reported a marginal survivor on
Tears of Steel, because the numbers came from ClickHouse's `rankCorr`, which does not
average tied ranks. The content scores take seven to nine distinct values across
eighty-four segments, so ties are most of the data and the two methods disagree by up to
0.083 — enough to move a field across the floor. `analyze-film.py` had it right all
along, and the disagreement between the two is what surfaced it.

Two supporting details worth keeping:

- The pilot's "all six point the right way" was a small-sample accident; at n=84 the
  directions within a single film disagree.
- Three clips are title or credit cards — near-identical content — and they landed in
  **both** extreme groups (bin 0 HIGH, bin 9 LOW, bin 96 HIGH). Nothing demonstrates
  edge noise more cheaply than that.

---

## What the product is now

**Not:** "we predict which stretches lose your audience."

**Instead:** attention data and content analysis have never been in the same queryable
place, because the companies that hold playback data do not build editing tools and the
companies that build editing tools cannot get playback data. Second Pass puts them in
one table and lets a human interrogate the join — *and it reports how strong the
relationship actually is, including when the answer is "weak".*

The falsification machinery was always the differentiator. The day-1 result makes it the
product:

> **Proof panel, first entry.** On Tears of Steel (n=84), two content fields correlate
> significantly with raw attention and neither survives correcting for position; nothing
> takes their place. On Big Buck Bunny one field clears the corrected floor by 0.027,
> which is marginal. Five of five comparable fields agree in direction across the two
> films at p = 0.063 — suggestive, not significant. Position explains more than content
> does. Here is the method, here is the data, here is the script that reproduces it, and
> here is the same query against shuffled attention finding nothing at all.

A system willing to show that about itself is more credible than one that claims
prediction. And the claim it *does* make — that the two datasets belong together and
that position is the dominant confound nobody controls for — is supported by exactly the
evidence above.

### What a user actually does

1. Point it at a film that has public attention data.
2. It segments, detrends, scores each segment with Gemini, and loads all of it into
   ClickHouse.
3. Ask questions across the join: *which stretches underperform their position?
   do the ones with no dialogue underperform? is that true across three films or just
   this one?*
4. The Proof panel reports the correlation, the sample size, and the negative control —
   never a verdict dressed up as certainty.

---

## Architecture

```
ingest/fetch      yt-dlp -> heatmap JSON (cache it; undocumented endpoint)
                  film from download.blender.org (CC-BY)
                  NOTE: 24 rapid ranged requests to blender.org returns 429.
                  Download once, cut locally.
ingest/segment    heatmap bins + detrend (moving average, +-8)
ingest/score      per bin: 7s @ 360p/2fps WITH audio, inline to Gemini,
                  blind prompt, six scores
                  NOTE: free-tier quota dies around 20 video calls. Retry with
                  backoff, write progress after every clip, skip already-scored
                  bins on rerun. This is why scoring 300 segments is a schedule
                  item, not an afternoon.
store             ClickHouse: segments x features x attention  (partner requirement)
                  writes via clickhouse-connect; mcp-clickhouse is read-only
agent             ADK + McpToolset -> mcp-clickhouse, parameterised query templates
                  through run_query so MCP is genuinely in the path
ui                film player, attention curve, residual curve, Proof panel
```

**Why ClickHouse is not decoration here:** the questions are all joins across
segment features and per-second attention, over multiple films, answered inside a
conversation. That is an OLAP shape, and the alternative is a spreadsheet.

---

## 10-day plan

Day 1 (done): gate run, result recorded, `analyze.py` reproduces it.

**D2–D3 — data at n that can answer something**
- [ ] Score all 100 bins on Tears of Steel, then two more films. ~300 video calls
      against a quota that dies at ~20 — this is the schedule's real constraint.
      Run it as a background trickle from day 2, not the night before.
- [ ] $100 **Google Cloud** hackathon credit form (https://forms.gle/XPe837tzogh8L5sX6)
      — 1-5 business days, while supplies last, no stated expiry. Optional: the
      deploy project already carries credit. An earlier draft of this line called
      it a ClickHouse coupon expiring 8/31; there is no such offer. ClickHouse's
      side is the standard $300 Cloud trial, claimed at signup, nothing to file.
- [ ] ClickHouse Cloud trial

**D4–D5 — ClickHouse**
- [ ] `segments`, `segment_features`, `attention` tables; load via `clickhouse-connect`
- [ ] `mcp-clickhouse` via ADK `McpToolset` (`StdioConnectionParams` **wraps**
      `StdioServerParameters`; prefer HTTP transport when deployed)

**D6–D7 — the product**
- [ ] Ask-across-the-join agent, answers grounded in query results
- [ ] Proof panel: correlation, n, and the negative control (shuffle the attention
      column, confirm the agent stops finding explanations)

**D8–D9 — submission**
- [ ] Deploy (reusing the Cloud Run pipeline from an earlier project of mine), **≤3 min** video (Cinema is 3, not 4)
- [ ] Public repo + OSI licence, Devpost text

**D10 — buffer**

---

## Known constraints, carried forward

- Heatmap is **not** in the YouTube Data API — scraped, undocumented, cache it.
  Position the product honestly: public rewatch data is the demo input; a channel
  owner's own `audienceWatchRatio` is the production input and is strictly better.
- The heatmap measures **rewatch, not exit**. Frame everything as attention.
- **Nielsen US9886981B2** covers telemetry → automatic removal of weak segments.
  Stay on the analysis side, not the auto-recut side.
- Agentic Cinema **forbids non-Google AI** — if any code is lifted from
  `agentic-cinema/`, the `anthropic` adapter cannot come with it.
- Agentic Cinema requires the project be created inside 7/27–9/7 and says it must not be
  "a modification or extension of Your existing work". This is a new codebase, which
  helps; disclose anything reused.
- ffmpeg under a zh-CN locale rejects decimal `-ss` / `-t`. Use integer seconds.
- Python `print` on Windows emits `\r\n`; a `\r` reaching ffmpeg produces
  "Invalid duration" with the offending character invisible in the error. Pipe through
  `tr -d '\r'`.
