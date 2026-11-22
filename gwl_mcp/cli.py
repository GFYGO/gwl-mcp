"""命令行入口：stdio / Streamable HTTP / SSE 三种启动方式与自检。

用法：

    python run.py                         # stdio（默认，给本地 MCP Client 用）
    python run.py --transport http        # Streamable HTTP，默认 http://127.0.0.1:8765/mcp
    python run.py --transport sse         # 旧版 SSE 传输（/sse + /messages/）
    python run.py --check                 # 只验证 GWL 凭据能否打通 /ping
    python run.py --list-tools            # 打印 tools/list 的结果（JSON）
    python run.py --version

所有配置都来自环境变量（或 --env-file / GWL_ENV_FILE 指向的 .env），
命令行只负责选择传输方式与临时覆盖监听地址。
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import __version__
from .api import USER_AGENT
from .config import Config
from .errors import ConfigError, GwlError
from .log import configure


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gwl-mcp",
        description="GWL /api/v1 用户 API 的 MCP 服务（官方 Python SDK 实现）。",
    )
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "http", "streamable-http", "sse"],
        help="传输方式；默认 stdio（本地 MCP Client），http/sse 需要 GWL_HTTP_* 配置。",
    )
    parser.add_argument("--host", help="覆盖 GWL_HTTP_HOST（仅 http/sse）")
    parser.add_argument("--port", type=int, help="覆盖 GWL_HTTP_PORT（仅 http/sse）")
    parser.add_argument("--path", help="覆盖 GWL_HTTP_PATH（仅 http；sse 固定 /sse）")
    parser.add_argument("--env-file", help="额外加载的 .env 文件（等同 GWL_ENV_FILE）")
    parser.add_argument("--check", action="store_true", help="只验证凭据并退出（0=通过）")
    parser.add_argument("--list-tools", action="store_true", help="打印工具清单 JSON 并退出")
    parser.add_argument("--version", action="store_true", help="打印版本并退出")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.version:
        print(json.dumps({"name": "gwl-api-mcp", "version": __version__, "sdk": _sdk_version()},
                         ensure_ascii=False))
        return 0

    try:
        config = Config.from_env(env_file=args.env_file)
    except ConfigError as exc:
        # 配置错误必须可见；此时还没有进入 stdio 协议循环，写 stderr 安全
        print("[gwl-mcp] 配置错误：%s" % exc.message, file=sys.stderr)
        return 2

    configure(config.log_level)

    if args.list_tools:
        return _list_tools(config)
    if args.check:
        return _check(config)

    from .server import build_http_app, create_server, run_stdio

    if args.transport == "stdio":
        server = create_server(config)
        _note_stdio(config)
        return run_stdio(server)

    host = args.host or config.http_host
    port = args.port if args.port is not None else config.http_port
    path = args.path or config.http_path
    try:
        config_http = _with_overrides(config, host=host, port=port, path=path)
    except ConfigError as exc:
        print("[gwl-mcp] 配置错误：%s" % exc.message, file=sys.stderr)
        return 2

    server = create_server(config_http)
    app = build_http_app(server, config_http, transport="sse" if args.transport == "sse" else "http")
    if config_http.http_auth_token is None:
        print("[gwl-mcp] 警告：未设置 GWL_HTTP_AUTH_TOKEN，MCP 端点只靠绑定地址 %s 保护。"
              % config_http.http_host, file=sys.stderr)
    listen = "%s:%d%s" % (config_http.http_host, config_http.http_port, config_http.http_path)
    print("[gwl-mcp] %s 传输监听 http://%s（Ctrl+C 退出）" % (args.transport, listen), file=sys.stderr)

    import uvicorn

    try:
        uvicorn.run(app, host=config_http.http_host, port=config_http.http_port,
                    log_level=config_http.log_level.lower(), access_log=False)
    except (KeyboardInterrupt):
        return 0
    return 0


def _with_overrides(config: Config, *, host: str, port: int, path: str) -> Config:
    from dataclasses import replace

    return replace(config, http_host=host, http_port=port, http_path=path)


def _note_stdio(config: Config) -> None:
    print(
        "[gwl-mcp] stdio 服务已启动（上游 %s，用户 %s，写操作=%s）。日志只走 stderr。"
        % (config.base_url, config.user_id, "开" if config.enable_writes else "关"),
        file=sys.stderr,
    )


def _list_tools(config: Config) -> int:
    import anyio

    from .server import create_server

    async def run() -> dict[str, Any]:
        server = create_server(config)
        tools = await server.list_tools()
        return {
            "server": {"name": "gwl-api-mcp", "version": __version__},
            "count": len(tools),
            "tools": [tool.model_dump(by_alias=True, exclude_none=True) for tool in tools],
        }

    print(json.dumps(anyio.run(run), ensure_ascii=False, indent=2))
    return 0


def _check(config: Config) -> int:
    import anyio

    from .api import ApiClient
    from .runtime import Runtime

    async def run() -> int:
        api = ApiClient(config)
        runtime = Runtime(config=config, api=api)
        try:
            response = await api.arequest("GET", "/ping")
        except GwlError as exc:
            print(json.dumps({"ok": False, "message": exc.message,
                              **exc.to_payload()}, ensure_ascii=False, indent=2))
            return 1
        data = response.data if isinstance(response.data, dict) else {}
        print(json.dumps({
            "ok": True,
            "userid": data.get("userid"),
            "username": data.get("username"),
            "real_level": data.get("real_level"),
            "rate_limit": data.get("rate_limit"),
            "user_agent": USER_AGENT,
            "base_url": config.base_url,
            "tools": len(runtime.index) or 36,
        }, ensure_ascii=False, indent=2))
        return 0

    return anyio.run(run)


def _sdk_version() -> str:
    try:
        import importlib.metadata

        return importlib.metadata.version("mcp")
    except Exception:  # noqa: BLE001
        return "unknown"