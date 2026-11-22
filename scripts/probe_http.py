#!/usr/bin/env python3
"""验证 Streamable HTTP 传输：initialize / tools/list / tools/call / 鉴权 / 会话。

    终端 A：.venv\\Scripts\\python.exe run.py --transport http --port 8765
    终端 B：.venv\\Scripts\\python.exe scripts/probe_http.py [--url http://127.0.0.1:8765/mcp]
                                                        [--token 与 GWL_HTTP_AUTH_TOKEN 一致]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V1))


async def probe(url: str, token: str | None) -> int:
    from mcp import Client
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
    from mcp.client.session import ClientSession

    headers = {"Authorization": "Bearer " + token} if token else {}
    http = create_mcp_http_client(headers=headers, timeout=__import__("httpx2").Timeout(30.0, read=120.0))
    async with http:
        async with streamable_http_client(url, http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                info = await session.initialize()
                print("协议版本：%s / 服务：%s %s"
                      % (info.protocol_version, info.server_info.name, info.server_info.version))
                tools = await session.list_tools()
                print("tools/list：%d 个" % len(tools.tools))
                if len(tools.tools) != 36:
                    print("FAIL：工具数不是 36", file=sys.stderr)
                    return 1
                resources = await session.list_resources()
                print("resources：%d 个" % len(resources.resources))
                result = await session.call_tool("gwl_ping", {})
                if result.is_error:
                    print("gwl_ping 被拒：%s" % result.content[0].text.splitlines()[0])
                else:
                    print("gwl_ping OK：%s" % json.loads(result.content[0].text)["data"].get("username"))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.environ.get("GWL_MCP_URL", "http://127.0.0.1:8765/mcp"))
    parser.add_argument("--token", default=os.environ.get("GWL_HTTP_AUTH_TOKEN"))
    args = parser.parse_args()
    print("目标端点：%s（令牌：%s）" % (args.url, "已设置" if args.token else "未设置"))
    return asyncio.run(probe(args.url, args.token))


if __name__ == "__main__":
    raise SystemExit(main())
