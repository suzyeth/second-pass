# Load measured segments and generate synthetic playback events.
#
#   set CLICKHOUSE_HOST=... CLICKHOUSE_PASSWORD=...
#   python load.py            # everything in films.json that has been scored
#   python load.py --dry-run  # generate and report, connect to nothing
#
# The events are synthetic and the schema says so. What makes them defensible
# rather than decorative is that they are not random: each session walks the film
# second by second, and its chance of leaving or of scrubbing back is driven by
# the REAL attention value at that point. Aggregate the generated events back up
# and you recover the curve they came from — which is checkable, and is checked
# at the end of this script. A generator whose output cannot be traced back to a
# real measurement is just noise with a schema.

import argparse
import io
import json
import math
import os
import random
import statistics as st

# 100k sessions against a film with 2.6M views is a plausible sample, and it is
# what turns this from a spreadsheet into a workload: ~4M events per film. The
# generator streams rather than building a list — holding 4M tuples in memory to
# then insert them is how this falls over on the third film.
SESSIONS_PER_FILM = 100_000
SEED = 20260827  # fixed: the same corpus every run, so a query result is stable


def load_film(film_id, films):
    film = films[film_id]
    meta = json.load(io.open(film["meta"], encoding="utf-8"))
    scored_path = f"films/{film_id}/scored.json"
    if not os.path.exists(scored_path):
        return None

    rows = [r for r in json.load(io.open(scored_path, encoding="utf-8")) if "inertness" in r]
    if not rows:
        return None

    values = [r["raw"] for r in sorted(rows, key=lambda r: r["bin"])]
    half = len(values) // 2
    first, last = st.mean(values[:half]), st.mean(values[half:])

    return {
        "id": film_id,
        "title": film["title"],
        "youtube": film["youtube"],
        "duration": float(meta["duration"]),
        "bins": len(rows),
        "position_bias": (last / first) if first else 0.0,
        "rows": rows,
    }


def generate_events(film, rng):
    """One session at a time, driven by the film's own attention curve.

    Two behaviours, because the heatmap measures both and conflating them is how
    'rewatch' gets mislabelled as 'retention':
      - leaving is more likely where attention is LOW
      - scrubbing back is more likely where attention is HIGH (that is what a
        most-replayed heatmap is actually recording)
    """
    by_bin = {r["bin"]: r for r in film["rows"]}
    bins = sorted(by_bin)
    lo = min(by_bin[b]["raw"] for b in bins)
    hi = max(by_bin[b]["raw"] for b in bins)
    span = (hi - lo) or 1.0

    for session in range(SESSIONS_PER_FILM):
        for b in bins:
            seg = by_bin[b]
            norm = (seg["raw"] - lo) / span  # 0..1 within this film

            # Base hazard of dropping out, damped where attention is high.
            # 0.004..0.024 per bin gives a survival curve that still has most
            # sessions alive at the end of a 100-bin film without being flat.
            if rng.random() < 0.024 - 0.020 * norm:
                yield (film["id"], session, int(seg["start"]), b, "exit")
                break

            yield (film["id"], session, int(seg["start"]), b, "watch")

            # Rewatch: the thing the heatmap is actually made of.
            if rng.random() < 0.35 * norm:
                yield (film["id"], session, int(seg["start"]), b, "seek_back")


def verify(film, events):
    """The generator's only real claim: aggregate its output and the input curve
    comes back. Reported, not assumed. Consumes the stream and counts as it goes,
    so nothing is held in memory that does not have to be."""
    seeks = {}
    total = 0
    for _, _, _, b, kind in events:
        total += 1
        if kind == "seek_back":
            seeks[b] = seeks.get(b, 0) + 1

    bins = sorted(r["bin"] for r in film["rows"])
    by_bin = {r["bin"]: r["raw"] for r in film["rows"]}
    observed = [seeks.get(b, 0) for b in bins]
    expected = [by_bin[b] for b in bins]

    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        out = [0] * len(v)
        for k, i in enumerate(order):
            out[i] = k
        return out

    rx, ry = rank(observed), rank(expected)
    n = len(bins)
    d2 = sum((rx[i] - ry[i]) ** 2 for i in range(n))
    return 1 - 6 * d2 / (n * (n * n - 1)), total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    films = json.load(io.open("films.json", encoding="utf-8"))
    rng = random.Random(SEED)

    loaded = []
    for film_id in films:
        film = load_film(film_id, films)
        if not film:
            print(f"{film_id}: not scored yet, skipping")
            continue

        rho, count = verify(film, generate_events(film, random.Random(SEED)))
        print(
            f"{film_id}: {film['bins']} segments, {count:,} events, "
            f"position_bias={film['position_bias']:.2f}, "
            f"generator fidelity rho={rho:+.3f}"
        )
        loaded.append((film, count))

    total = sum(c for _, c in loaded)
    print(f"\ntotal: {total:,} events across {len(loaded)} film(s)")

    if args.dry_run:
        print("dry run — nothing written")
        return

    import clickhouse_connect

    client = clickhouse_connect.get_client(
        host=os.environ["CLICKHOUSE_HOST"],
        port=int(os.environ.get("CLICKHOUSE_PORT", 8443)),
        username=os.environ.get("CLICKHOUSE_USER", "default"),
        password=os.environ["CLICKHOUSE_PASSWORD"],
        secure=os.environ.get("CLICKHOUSE_SECURE", "1") != "0",
    )

    for statement in io.open("schema.sql", encoding="utf-8").read().split(";"):
        if statement.strip():
            client.command(statement)

    for film, _count in loaded:
        client.insert(
            "films",
            [[film["id"], film["title"], film["youtube"], film["duration"],
              film["bins"], film["position_bias"]]],
            column_names=["film", "title", "youtube_id", "duration_s", "bin_count", "position_bias"],
        )

        client.insert(
            "segments",
            [
                [
                    film["id"], r["bin"], r["start"], r["end"],
                    r["raw"], r["base"], r["res"], 1 if r.get("edge") else 0,
                    r["visual_event_density"], r["story_information"],
                    r["character_presence"], r["speech_density"],
                    r["score_intensity"], r["inertness"],
                    r.get("one_line", ""), "gemini-3.5-flash-lite",
                ]
                for r in film["rows"]
            ],
            column_names=[
                "film", "bin", "start_s", "end_s",
                "attention_raw", "attention_base", "attention_res", "is_edge",
                "visual_event_density", "story_information", "character_presence",
                "speech_density", "score_intensity", "inertness",
                "one_line", "scored_model",
            ],
        )

        # Chunked: one insert of several million rows is a timeout waiting to
        # happen on a trial-tier instance.
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
        CHUNK = 200_000
        batch, written = [], 0
        for f, sess, t, b, k in generate_events(film, random.Random(SEED)):
            batch.append([f, sess, t, b, k, now])
            if len(batch) >= CHUNK:
                client.insert("playback_events", batch,
                    column_names=["film", "session_id", "t_s", "bin", "event", "ts"])
                written += len(batch)
                batch = []
        if batch:
            client.insert("playback_events", batch,
                column_names=["film", "session_id", "t_s", "bin", "event", "ts"])
            written += len(batch)
        print(f"{film['id']}: inserted {written:,} events")

    print("\ncounts:", client.query("SELECT film, count() FROM playback_events GROUP BY film").result_rows)


if __name__ == "__main__":
    main()
