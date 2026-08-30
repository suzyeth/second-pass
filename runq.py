# Run queries.sql and print each block with its comment, timing, and rows read.
#
#   python runq.py
#
# Rows-read is printed because it is the honest measure of whether ClickHouse is
# doing anything: a query that answers in 30ms after touching four million rows
# is a different claim from one that touched ninety-five.

import io
import os
import time

import clickhouse_connect


def main():
    client = clickhouse_connect.get_client(
        host=os.environ["CLICKHOUSE_HOST"],
        port=int(os.environ.get("CLICKHOUSE_PORT", 8443)),
        username=os.environ.get("CLICKHOUSE_USER", "default"),
        password=os.environ["CLICKHOUSE_PASSWORD"],
        secure=True,
    )

    sql = io.open("queries.sql", encoding="utf-8").read()

    for block in sql.split(";"):
        block = block.strip()
        if "SELECT" not in block.upper():
            continue

        heading = [
            line.strip()[3:]
            for line in block.split("\n")
            if line.strip().startswith("--")
        ]

        print("\n" + "=" * 78)
        for line in heading:
            print(line)
        print("-" * 78)

        started = time.time()
        result = client.query(block)
        elapsed = (time.time() - started) * 1000

        widths = []
        rows = [[str(v)[:46] for v in row] for row in result.result_rows]
        for i, name in enumerate(result.column_names):
            widths.append(max([len(name)] + [len(r[i]) for r in rows]) if rows else len(name))

        print("  ".join(n.ljust(w) for n, w in zip(result.column_names, widths)))
        for row in rows:
            print("  ".join(v.ljust(w) for v, w in zip(row, widths)))

        read = result.summary.get("read_rows", "?")
        print(f"\n  {elapsed:.0f} ms, {int(read):,} rows read" if str(read).isdigit()
              else f"\n  {elapsed:.0f} ms")


if __name__ == "__main__":
    main()
