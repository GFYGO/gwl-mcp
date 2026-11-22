#!/usr/bin/env python3
"""用官方 SDK 客户端通过 **子进程 stdio** 完整验证一次接入链路。

这是「真实 MCP Client」级别的验收：起的就是配置文件里那条命令。

    .venv\\Scripts\\python.exe scripts/probe_stdio.py
    .venv\\Scripts\\python.exe scripts/probe_stdio.py --skip-live   # 不碰上游，只验协议

退出码 0 = 全部通过。任何一步失败都会打印实际收到的内容。
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

EXPECTED_TOOLS = 36


async def run(live: bool) -> int:
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    # 没有真实凭据也要能起服务：注入占位值（形如 gk_ + 43 字符），
    # 这样 --skip-live 只验协议链路，不碰上游。
    if not env.get("GWL_USER_ID"):
        env["GWL_USER_ID"] = "12"
    if not env.get("GWL_API_KEY"):
        env["GWL_API_KEY"] = "gk_" + "x" * 43
        print("提示：未设置 GWL_API_KEY，已注入占位凭据（gwl_ping 预期被上游拒绝）")
    if env.get("GWL_API_BASE", "").startswith("http://"):
        env.setdefault("GWL_ALLOW_HTTP", "1")
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(V1 / "run.py"), "--transport", "stdio"],
        env=env,
        cwd=str(V1),
    )
    print("子进程命令：%s %s run.py --transport stdio" % (sys.executable, V1))
    async with Client(params) as client:
        listing = await client.list_tools()
        names = sorted(tool.name for tool in listing.tools)
        print("tools/list：%d 个工具" % len(names))
        if len(names) != EXPECTED_TOOLS:
            print("FAIL：期望 %d 个" % EXPECTED_TOOLS, file=sys.stderr)
            return 1
        sample = [tool for tool in listing.tools if tool.name == "gwl_search"][0]
        print("gwl_search inputSchema.required =", sample.input_schema.get("required"))

        resources = await client.list_resources()
        templates = await client.list_resource_templates()
        print("resources：%d 个静态 + %d 个模板"
              % (len(resources.resources), len(templates.resource_templates)))

        ping = await client.call_tool("gwl_ping", {})
        if ping.is_error:
            if live:
                print("FAIL gwl_ping：%s" % ping.content[0].text, file=sys.stderr)
                return 1
            print("gwl_ping（未配置真实凭据，预期被拒）：%s" % ping.content[0].text.splitlines()[0])
        else:
            data = json.loads(ping.content[0].text)["data"]
            print("gwl_ping OK：userid=%s username=%s level=%s rate_limit=%s"
                  % (data.get("userid"), data.get("username"), data.get("real_level"),
                     json.dumps(data.get("rate_limit"), ensure_ascii=False)))

        bad = await client.call_tool("gwl_list_documents", {"page_size": 5})
        print("未知参数被拒（应为 isError）：%s | %s"
              % (bad.is_error, bad.content[0].text.splitlines()[0][:80]))
        if not bad.is_error:
            return 1

        inventory = await client.read_resource("gwl://tools/inventory")
        rows = json.loads(inventory.contents[0].text)["tools"]
        print("资源 gwl://tools/inventory：%d 行端点对照" % len(rows))
    print("OK：stdio 链路全部通过")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-live", action="store_true", help="不要求真实凭据可用")
    args = parser.parse_args()
    return asyncio.run(run(live=not args.skip_live))


if __name__ == "__main__":
    raise SystemExit(main())
