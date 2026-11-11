"""Teste de fumaça do servidor MCP: python tests/mcp_smoke.py http://127.0.0.1:8766/mcp"""
import asyncio
import json
import sys

from mcp import Client


def show(name, res):
    txt = res.structured_content if getattr(res, "structured_content", None) is not None else \
        [c.text for c in res.content if hasattr(c, "text")]
    s = json.dumps(txt, ensure_ascii=False, default=str)
    print(f"{name}{' [ERRO]' if res.is_error else ''}: {s[:600]}")


async def main(url):
    async with Client(url) as c:
        tools = await c.list_tools()
        print("tools:", [t.name for t in tools.tools])
        show("status", await c.call_tool("pdpower_status", {}))
        show("set", await c.call_tool("pdpower_set", {"voltage": 3.3, "current": 0.5}))
        show("set fora de faixa", await c.call_tool("pdpower_set", {"voltage": 30}))
        show("output on", await c.call_tool("pdpower_set_output", {"on": True}))
        show("measure", await c.call_tool("pdpower_measure", {"seconds": 1}))
        show("sweep", await c.call_tool("pdpower_sweep", {"param": "v", "start": 1, "stop": 3, "step": 0.5,
                                                          "dwell": 0.3, "fixed": 1.0}))
        show("history", await c.call_tool("pdpower_history", {"seconds": 10, "max_points": 5}))
        show("raw", await c.call_tool("pdpower_raw_read", {"command_hex": "85"}))
        show("raw write blocked", await c.call_tool("pdpower_raw_read", {"command_hex": "02 01"}))
        show("configure", await c.call_tool("pdpower_configure", {"brightness": 40, "ocp": True}))
        show("stop", await c.call_tool("pdpower_stop", {}))


asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8766/mcp"))
