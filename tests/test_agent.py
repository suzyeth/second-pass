import asyncio

import pytest

import agent


def test_mcp_query_timeout_cancels_abandoned_future(monkeypatch):
    async def scenario():
        client = agent.ClickHouseMCP()
        client._task = object()
        client._queue = asyncio.Queue()
        monkeypatch.setattr(agent, "MCP_QUERY_TIMEOUT_S", 0.001)

        with pytest.raises(TimeoutError, match="ClickHouse MCP query exceeded"):
            await client.run_query("SELECT sleep(1)")

        _, future = await client._queue.get()
        assert future.cancelled()

    asyncio.run(scenario())


def test_mcp_server_defaults_to_tls_on_8443(monkeypatch):
    monkeypatch.setenv("CLICKHOUSE_HOST", "h")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "p")
    monkeypatch.delenv("CLICKHOUSE_PORT", raising=False)
    monkeypatch.delenv("CLICKHOUSE_SECURE", raising=False)
    env = agent.ClickHouseMCP.server_env()
    assert env["CLICKHOUSE_PORT"] == "8443"
    assert env["CLICKHOUSE_SECURE"] == "true"


def test_mcp_server_can_target_a_local_clickhouse(monkeypatch):
    monkeypatch.setenv("CLICKHOUSE_HOST", "localhost")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "")
    monkeypatch.setenv("CLICKHOUSE_PORT", "8123")
    monkeypatch.setenv("CLICKHOUSE_SECURE", "false")
    env = agent.ClickHouseMCP.server_env()
    assert (env["CLICKHOUSE_HOST"], env["CLICKHOUSE_PORT"], env["CLICKHOUSE_SECURE"]) == (
        "localhost", "8123", "false")
