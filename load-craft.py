# Load craft.tsv into ClickHouse.
#
#   python load-craft.py            # rebuild craft_videos from craft.tsv
#   python load-craft.py --dry-run  # parse and report, connect to nothing
#
# Separate from load.py on purpose. The two corpora answer different questions and
# change on different schedules: the film corpus is rebuilt when a film is rescored,
# this one only when the demo-video sample is re-collected. Loading them together
# would mean a rescore silently reloads a corpus that did not change, and a
# TRUNCATE that nobody asked for is how a table gets emptied by accident.
#
# TRUNCATE-then-insert for the same reason load.py does it: MergeTree does not
# deduplicate, so a second run without it silently doubles every row and every
# percentile computed off the table moves.

import argparse
import io
import os

TAB = chr(9)
NL = chr(10)

# Columns that are empty when a video has no captions, or when the pattern was
# never found. An empty cell becomes NULL, never 0: a video whose transcript
# never says "let me show you" has no first-demo-verb time, and writing 0 there
# would claim it started demonstrating at the very first second.
NULLABLE_NUMERIC = {
    "words_total", "wpm", "sentence_words",
    "words_first_5s", "words_first_10s", "words_first_15s", "words_first_20s",
    "intro_first_15s", "names_project_first_15s", "problem_first_15s",
    "demo_verb_at_s", "demo_verb_frac", "tech_first_frac",
    "mentions_number", "multi_voice",
    "thanks_at_end", "cta_at_end", "tail_wpm", "silent_tail",
}
INTEGER = {
    "duration_s", "has_captions",
    "words_total", "words_first_5s", "words_first_10s", "words_first_15s",
    "words_first_20s", "intro_first_15s", "names_project_first_15s",
    "problem_first_15s", "mentions_number", "multi_voice",
    "thanks_at_end", "cta_at_end", "silent_tail",
}


def parse(path):
    lines = io.open(path, encoding="utf-8").read().rstrip(NL).split(NL)
    header = lines[0].split(TAB)
    rows = []
    for line in lines[1:]:
        cells = line.split(TAB)
        row = []
        for col, cell in zip(header, cells):
            cell = cell.strip()
            if col in ("video_id", "batch"):
                row.append(cell)
            elif cell == "":
                if col not in NULLABLE_NUMERIC:
                    raise SystemExit("empty %s for %s, and it is not nullable"
                                     % (col, cells[0]))
                row.append(None)
            else:
                row.append(int(float(cell)) if col in INTEGER else float(cell))
        rows.append(row)
    return header, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="craft.tsv")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    header, rows = parse(args.file)
    subs = sum(1 for r in rows if r[header.index("has_captions")])
    by_batch = {}
    for r in rows:
        b = r[header.index("batch")]
        by_batch[b] = by_batch.get(b, 0) + 1
    print("%s: %d videos, %d with captions  (%s)"
          % (args.file, len(rows), subs,
             ", ".join("%s %d" % kv for kv in sorted(by_batch.items()))))

    if args.dry_run:
        print("dry run - nothing written")
        return

    import clickhouse_connect

    client = clickhouse_connect.get_client(
        host=os.environ["CLICKHOUSE_HOST"],
        port=int(os.environ.get("CLICKHOUSE_PORT", 8443)),
        username=os.environ.get("CLICKHOUSE_USER", "default"),
        password=os.environ["CLICKHOUSE_PASSWORD"],
        secure=os.environ.get("CLICKHOUSE_SECURE", "1") != "0",
    )

    schema = io.open("schema.sql", encoding="utf-8").read()
    schema = NL.join(line.split("--")[0] for line in schema.splitlines())
    for statement in schema.split(";"):
        if "craft_videos" in statement:
            client.command(statement)

    client.command("TRUNCATE TABLE IF EXISTS craft_videos")
    client.insert("craft_videos", rows, column_names=header)

    got = client.query(
        "SELECT count() AS n, uniqExact(video_id) AS ids, "
        "countIf(has_captions = 1) AS with_captions, "
        "round(median(duration_s)) AS median_duration, "
        "round(median(wpm)) AS median_wpm FROM craft_videos"
    ).result_rows[0]
    n, ids, cap, dur, wpm = got
    print("craft_videos: %d rows, %d distinct ids, %d with captions" % (n, ids, cap))
    if n != ids:
        raise SystemExit("loaded more rows than ids - the table was loaded twice")
    print("median duration %ds, median %d words/min" % (dur, wpm))


if __name__ == "__main__":
    main()
