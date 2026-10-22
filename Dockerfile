# The image carries three programs: the server, the mcp-clickhouse MCP server it
# spawns as a subprocess and talks to over stdio, and the ClickHouse server that
# MCP queries. The last one used to be ClickHouse Cloud. Its trial ended, so the
# database now ships inside the image, already loaded, and the agent still
# reaches it only through MCP.
#
# A slim base without Python on PATH would break the MCP subprocess silently,
# with the server healthy and every query failing, so the base stays
# python:3.12-slim.

# The official image is used only as a source for one static binary, pinned to
# an LTS release so that a rebuild next month is the same database.
FROM clickhouse/clickhouse-server:25.8.33.6 AS clickhouse

FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    # The banner is noise in Cloud Logging; the per-query lines below it are the
    # record of what MCP actually executed, and those stay.
    FASTMCP_SHOW_CLI_BANNER=false \
    # ADK would otherwise try Vertex with application-default credentials.
    GOOGLE_GENAI_USE_VERTEXAI=FALSE \
    # The bundled server: loopback, plain HTTP, no password (clickhouse/users.xml).
    CLICKHOUSE_HOST=127.0.0.1 \
    CLICKHOUSE_PORT=8123 \
    CLICKHOUSE_USER=default \
    CLICKHOUSE_PASSWORD= \
    CLICKHOUSE_SECURE=false

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --from=clickhouse /usr/bin/clickhouse /usr/bin/clickhouse
COPY clickhouse/config.xml clickhouse/users.xml /etc/clickhouse-server/

# --- data: loaded once, at build time ----------------------------------------
#
# load.py generates the synthetic events from a fixed seed, so this is the same
# thirteen million rows every build. Loading here rather than at startup keeps
# cold start at the cost of opening the parts (about a quarter of a second)
# instead of the half minute the load takes.
FROM runtime AS data

COPY schema.sql load.py load-craft.py films.json tos.json bbb.json sintel.json craft.tsv ./
COPY films/tos/scored.json films/tos/
COPY films/bbb/scored.json films/bbb/
COPY films/sintel/scored.json films/sintel/

RUN clickhouse server --config-file=/etc/clickhouse-server/config.xml & \
    pid=$!; \
    i=0; \
    until clickhouse client --query "SELECT 1" >/dev/null 2>&1; do \
        i=$((i + 1)); [ "$i" -lt 60 ] || exit 1; sleep 1; \
    done; \
    CLICKHOUSE_SECURE=0 python load.py && CLICKHOUSE_SECURE=0 python load-craft.py \
    || exit 1; \
    kill "$pid"; wait "$pid" || true; \
    rm -rf /var/lib/clickhouse/tmp/*

# --- the service -------------------------------------------------------------
FROM runtime

COPY --from=data /var/lib/clickhouse /var/lib/clickhouse
COPY agent.py server.py tracing.py start.sh ./
COPY web/ ./web/

# Cloud Run supplies PORT; 8080 is the default it uses and what server.py falls
# back to when run locally.
EXPOSE 8080
CMD ["sh", "start.sh"]
