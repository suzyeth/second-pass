#!/bin/sh
# Container entrypoint: the bundled ClickHouse first, then the web server.
#
# The database was loaded at build time, so starting it is only opening parts
# that already exist on disk. The server is not held back if ClickHouse fails to
# come up: /api/health stays live, /api/ready reports the dependency as down,
# and the logs say why. That is the same contract the service had with
# ClickHouse Cloud, where the database could also be unreachable.

set -u

CH_WAIT_S="${CH_WAIT_S:-30}"

clickhouse server --config-file=/etc/clickhouse-server/config.xml &

i=0
until clickhouse client --query "SELECT 1" >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -ge "$CH_WAIT_S" ]; then
        echo "[start] clickhouse not answering after ${CH_WAIT_S}s; starting the server anyway" >&2
        break
    fi
    sleep 1
done
[ "$i" -lt "$CH_WAIT_S" ] && echo "[start] clickhouse up after ${i}s"

exec python server.py
