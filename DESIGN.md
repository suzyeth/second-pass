# Second Pass — attention and content, in one queryable place

Target: **Agentic Cinema**, ClickHouse track. Deadline **2026-09-07 14:00 PT**.
Written 2026-08-27, after the day-1 gate was run and did not pass.
Supersedes the 2026-08-08 concept in `G:\2026claude\agentic-cinema-DESIGN-abandoned.md`.

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

Excluding bins near either end, where a one-sided moving-average baseline makes the
residual an artifact rather than a measurement (`analyze.py` reports both):

| field | LOW | HIGH | diff | rho |
| --- | --- | --- | --- | --- |
| visual_event_density | 3.91 | 5.83 | **+1.92** | +0.316 |
| speech_density | 0.91 | 2.83 | **+1.92** | +0.164 |
| story_information | 3.36 | 4.33 | +0.97 | +0.118 |
| character_presence | 4.45 | 5.33 | +0.88 | −0.191 |
| score_intensity | 5.64 | 5.67 | +0.03 | −0.059 |
| inertness | 5.00 | 4.50 | −0.50 | −0.223 |

n=17. Strongest |rho| = 0.316; p<0.05 needs 0.490.

**All six point the right way** — more happens, more is said, more is revealed in the
stretches that hold attention better than their position predicts. But the effects are
too small to separate from noise at this n. The honest sentence is *consistent
direction, insufficient power*, not *no relationship*.

Two supporting details worth keeping:

- On the **full** sample four of six fields point the *wrong* way; excluding the edges
  fixes all four. The edges really are noise.
- Three clips are title or credit cards — near-identical content — and they landed in
  **both** groups (bin 0 HIGH, bin 9 LOW, bin 96 HIGH). Nothing demonstrates edge noise
  more cheaply than that.

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

> **Proof panel, first entry.** On Tears of Steel, after controlling for position,
> content explains attention only weakly (strongest ρ = 0.32, n = 17, not significant).
> Position explains far more than content does. Here is the method, here is the data,
> here is the script that reproduces it.

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
- [ ] $100 Agentic Cinema credit form — **coupon must be redeemed by 8/31**
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
