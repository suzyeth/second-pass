# Operations

## Runtime

The API and web UI run in one Python container. The process starts an official
`mcp-clickhouse` subprocess and communicates with it over stdio. ClickHouse and
Gemini credentials are provided at runtime; no secrets are included in the image.

Required environment variables:

- `CLICKHOUSE_HOST`
- `CLICKHOUSE_PASSWORD`
- `GOOGLE_API_KEY`

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
```

The test suite replaces ClickHouse MCP with an in-process fake and does not need
credentials or network access.

## Expected Failures

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
3. Check `/api/ready`; if it fails, verify ClickHouse credentials and service
   availability before replacing a healthy application revision.
4. For model 429/503 responses, confirm the fallback ladder was exhausted before
   retrying the request.

## Current Boundary

The public demo is single-purpose and exposes no private corpus. It does not yet
implement user authentication, per-principal rate limits, or persistent audit
storage. Those controls are required before presenting it as a multi-user
enterprise service.
