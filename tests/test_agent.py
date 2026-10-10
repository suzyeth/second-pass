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
