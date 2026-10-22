# Operations

## Runtime

The API, the web UI and the database run in one container. `start.sh` starts
ClickHouse (25.8 LTS, loopback only, data loaded at build time), waits for it to
answer, then starts the Python server. The server starts an official
`mcp-clickhouse` subprocess and communicates with it over stdio, and every query
reaches ClickHouse through it. The Gemini key is provided at runtime; no secrets
are included in the image.

Required environment variables:

- `GOOGLE_API_KEY`

Set by the image for the bundled database, and overridable to point at another
ClickHouse: `CLICKHOUSE_HOST` (`127.0.0.1`), `CLICKHOUSE_PORT` (`8123`),
`CLICKHOUSE_USER` (`default`), `CLICKHOUSE_PASSWORD` (empty),
`CLICKHOUSE_SECURE` (`false`).

Resources: 2 GiB. ClickHouse is about 600 MB resident when idle, and its own
caps are in `clickhouse/config.xml` (900 MB server, 600 MB per query). The data
directory is about 80 MB, and Cloud Run's writable filesystem is memory, so the
system log tables are not configured.

Optional controls:

- `PORT` (default `8080`)
- `GEMINI_MODEL`
- `MCP_QUERY_TIMEOUT_S` (default `30`)
- `READINESS_TIMEOUT_S` (default `10`)
- `LOG_LEVEL` (default `INFO`)

## Health Contract

- `GET /api/health` is a liveness endpoint. It proves the process can serve HTTP
  and does not depend on an external service.
- `GET /api/ready` executes `SELECT 1` through the MCP path. It returns `503` when
  the data dependency is unavailable or exceeds the readiness timeout.
- Every response includes `X-Request-ID`. The service logs one JSON record with
  the request id, method, path, status, and elapsed time.

## Local Verification

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
docker build -t second-pass:local .
docker run --rm -p 8080:8080 second-pass:local   # /api/films works without a key
```

The test suite replaces ClickHouse MCP with an in-process fake and does not need
credentials or network access.

## Expected Failures

- If the bundled ClickHouse does not answer within `CH_WAIT_S` (default 30)
  seconds, `start.sh` logs it and starts the server anyway.
- A ClickHouse outage makes readiness fail and query-backed endpoints return an
  error; liveness remains healthy so a rollout is not restarted indefinitely for
  an external outage.
- MCP queries are bounded by `MCP_QUERY_TIMEOUT_S`. A timed-out caller is
  cancelled without terminating the long-lived MCP worker.
- Invalid film identifiers are rejected before they can reach a query template.
- Questions over 2,000 characters are rejected before invoking the model.
- Transient model failures follow the retry and fallback ladder in `agent.py`.

## Recovery

1. Use the request id to find the failed request in platform logs.
2. Check `/api/health`; if it fails, restart or roll back the revision.
3. Check `/api/ready`; if it fails, read the container's startup lines for
   `[start]` and ClickHouse errors. The database is inside the revision, so a
   new revision rebuilt from the same commit restores it exactly.
4. For model 429/503 responses, confirm the fallback ladder was exhausted before
   retrying the request.

## Current Boundary

The public demo is single-purpose and exposes no private corpus. It does not yet
implement user authentication, per-principal rate limits, or persistent audit
storage. Those controls are required before presenting it as a multi-user
enterprise service.
