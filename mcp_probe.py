# Does mcp-clickhouse actually see our tables?
#
#   python mcp_probe.py
#
# This runs before any agent code, because the track requirement is not "uses
# ClickHouse" but "uses ClickHouse at runtime via the official MCP server". If
# the MCP server cannot reach this instance, nothing built on top of it counts,
# and it is better to find that out now than after an agent is wired to it.
#
# It also prints the tool list rather than assuming it: the server exposes three
# tools and is read-only by default, which is why ingestion goes through
# clickhouse-connect and only queries go through here.

import asyncio
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    params = StdioServerParameters(
        command="python",
        args=["-m", "mcp_clickhouse.main"],
        env={
            **os.environ,
            "CLICKHOUSE_HOST": os.environ["CLICKHOUSE_HOST"],
            "CLICKHOUSE_PORT": os.environ.get("CLICKHOUSE_PORT", "8443"),
            "CLICKHOUSE_USER": os.environ.get("CLICKHOUSE_USER", "default"),
            "CLICKHOUSE_PASSWORD": os.environ["CLICKHOUSE_PASSWORD"],
            "CLICKHOUSE_SECURE": "true",
        },
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("tools exposed:")
            for tool in tools.tools:
                print(f"  - {tool.name}")

            print("\ndatabases:")
            result = await session.call_tool("list_databases", {})
            print(" ", str(result.content[0].text)[:200])

            print("\nour tables:")
            result = await session.call_tool("list_tables", {"database": "default"})
            text = str(result.content[0].text)
            for name in ("films", "segments", "playback_events"):
                print(f"  {name:18} {'FOUND' if name in text else 'MISSING'}")

            print("\nthe product question, through MCP:")
            sql = (
                "SELECT bin, round(attention_res,3) AS underperformance, "
                "visual_event_density AS evt, substring(one_line,1,40) AS what "
                "FROM segments WHERE film='tos' AND is_edge=0 "
                "ORDER BY attention_res ASC LIMIT 3"
            )
            result = await session.call_tool("run_query", {"query": sql})
            print(" ", str(result.content[0].text)[:400])


asyncio.run(main())
