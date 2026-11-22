"""MCP 服务的组装与启动（官方 SDK mcp 2.x 的 MCPServer）。

结构：

    Config ─▶ ApiClient ─▶ Runtime（lifespan 产物） ─▶ MCPServer
                                                        ├─ tools（gwl_mcp.tools，36 个）
                                                        ├─ resources（gwl_mcp.resources，7 个）
                                                        └─ transports
                                                           ├─ stdio（默认，本地 MCP Client）
                                                           └─ Streamable HTTP（可Bearer 令牌保护）

日志：任何入口第一件事都是 log.configure()，保证日志只走 stderr。
"""
from __future__ import annotations

import hmac
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from . import __version__
from .api import ApiClient
from .config import Config
from .registry import install as install_tools
from .resources import register as register_resources
from .runtime import Runtime
from . import tools as _tools_pkg  # 触发工具声明收集（导入即注册）

SERVER_NAME = "gwl-api-mcp"

INSTRUCTIONS = (
    "这是 GFYGO（gwl.net.cn）的用户 API MCP 服务，全部工具都以「当前 API Key 所属用户」的身份执行，"
    "权限由服务端实时判定。\n"
    "使用顺序建议：\n"
    "1) 第一次调用请走 gwl_ping，确认凭据可用并读到上游限流口径（读 120/分、写 30/分、单 IP 240/分）；\n"
    "2) 找 cid 用 gwl_list_my_classes，找文档先用 gwl_list_documents / gwl_search 拿 id；\n"
    "3) 读文档正文优先 gwl_get_document_by_id（不计数）；gwl_get_document 会让 view_count +1；\n"
    "4) 更新文档前必须先读到 version，并把 version 原样带回 gwl_update_document，"
    "否则并发编辑会返回 409（409 的 data 里有 current_version，重新读取后再提交）；\n"
    "5) 标签写入是「全量替换」：先 gwl_get_object_tags 读回再合并提交；\n"
    "6) 不可恢复的永久删除接口刻意没有封装成工具，请勿尝试绕路删除。\n"
    "参数请严格照 inputSchema 传：未知参数会被拒绝。写操作可通过 GWL_ENABLE_WRITES=0 一键关闭。\n"
    "错误都以 isError=true 返回，首行是中文原因，后面附带 HTTP 状态、业务 code、Retry-After 等结构化信息。"
)


def create_server(config: Config, *, api_client: ApiClient | None = None) -> MCPServer:
    """构造一个装配好工具与资源的 MCPServer（尚未启动 lifespan）。"""

    runtime = Runtime(config=config, api=api_client or ApiClient(config))

    @asynccontextmanager
    async def lifespan(server: MCPServer) -> AsyncIterator[Runtime]:
        # 工具与资源都用同一个 runtime：静态资源的 handler 不允许注入 Context
        runtime.build_index(await server.list_tools())
        logging.getLogger("gwl_mcp").info(
            "服务启动：%s %s，工具 %d 个，上游 %s，写操作=%s",
            SERVER_NAME, __version__, len(runtime.index), config.base_url,
            "开" if config.enable_writes else "关",
        )
        try:
            yield runtime
        finally:
            logging.getLogger("gwl_mcp").info("服务退出：%s", SERVER_NAME)

    server: MCPServer = MCPServer(
        name=SERVER_NAME,
        title="GWL 用户 API（/api/v1）",
        description="把 GFYGO 的 userid + apikey 用户 API 封装成 MCP 工具。",
        version=__version__,
        instructions=INSTRUCTIONS,
        lifespan=lifespan,
        log_level=config.log_level,
    )
    install_tools(server)
    register_resources(server, runtime)
    return server


# ----------------------------------------------------------------------
# stdio
# ----------------------------------------------------------------------
def run_stdio(server: MCPServer) -> int:
    """stdio 传输：stdin/stdout 是协议通道，日志只走 stderr。"""
    import anyio

    try:
        anyio.run(server.run_stdio_async)
    except (KeyboardInterrupt, EOFError):
        return 0
    except BrokenPipeError:  # 客户端先退出
        return 0
    return 0


# ----------------------------------------------------------------------
# Streamable HTTP
# ----------------------------------------------------------------------
class BearerAuthMiddleware:
    """极简 Bearer / X-MCP-Token 校验（保护 MCP 端点本身，不是 GWL API Key）。

    只做一件事：没有令牌或令牌不对 → 401（JSON-RPC 形状的错误体）。
    会话、Origin/Host、协议头、CORS、SSE、批量等全部由官方 SDK 负责。
    """

    def __init__(self, app: Any, token: str):
        self.app = app
        self._token = token.encode("utf-8")

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = {k.lower(): v for k, v in scope.get("headers") or []}
            provided = b""
            auth = headers.get(b"authorization", b"").decode("latin-1").strip()
            if auth.lower().startswith("bearer "):
                provided = auth[7:].strip().encode("latin-1")
            elif auth.lower().startswith("apikey "):
                provided = auth[7:].strip().encode("latin-1")
            if not provided:
                provided = headers.get(b"x-mcp-token", b"")
            if not hmac.compare_digest(provided, self._token):
                await _unauthorized(send)
                return
        await self.app(scope, receive, send)


async def _unauthorized(send: Any) -> None:
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": None,
            "error": {
                "code": -32001,
                "message": "缺少或错误的 MCP 访问令牌：请使用 Authorization: Bearer <GWL_HTTP_AUTH_TOKEN>",
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": 401,
        "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"www-authenticate", b'Bearer realm="gwl-mcp"'),
            (b"cache-control", b"no-store"),
        ],
    })
    await send({"type": "http.response.body", "body": body})


def build_http_app(server: MCPServer, config: Config, *, transport: str = "http") -> Any:
    """产出交给 uvicorn 的 ASGI 应用（含访问令牌与 Host/Origin 防护）。

    transport="http" -> Streamable HTTP（单端点，SDK 自带会话 / SSE / CORS / 协议头校验）
    transport="sse"  -> 旧版 SSE 双端点（GET /sse + POST /messages/），给只认 SSE 的客户端
    """
    settings = dict(
        max_request_body_size=max(1, config.http_max_body_mb) * 1024 * 1024,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=_allowed_hosts(config),
            allowed_origins=_allowed_origins(config),
        ),
        host=config.http_host,
    )
    if transport == "sse":
        app = server.sse_app(sse_path="/sse", message_path="/messages/", **settings)

    else:
        app = server.streamable_http_app(
            streamable_http_path=config.http_path,
            stateless_http=False,
            json_response=config.http_json_response,
            session_idle_timeout=float(config.http_session_ttl),
            **settings,
        )

    if config.http_auth_token:
        app = BearerAuthMiddleware(app, config.http_auth_token)
    return app


#: 回环主机名：本机客户端在任何端口上访问都该放行（SDK 支持 host:* 通配端口）
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1", "[::1]")


def _allowed_hosts(config: Config) -> list[str]:
    """Host 头白名单（防 DNS rebinding）。

    规则：只允许回环主机名与操作者显式配置的监听地址，端口用 :* 通配
    （uvicorn 的实际监听端口常常与配置不同，例如测试用随机端口）。
    攻击者让自己的域名解析到 127.0.0.1 时 Host 头是那个域名，仍然会被拒。
    """
    configured = (config.http_host or "127.0.0.1").strip("[]")
    names = list(_LOOPBACK_HOSTS) + [configured, "0.0.0.0", "*"]
    out: list[str] = []
    for name in dict.fromkeys(names):
        if not name or name == "*":
            continue
        out.append(name)
        out.append("%s:*" % name)
    if configured == "0.0.0.0":
        # 绑到所有网卡时，客户端可能用任意本机 IP 访问；此时 Host 校验意义有限，
        # 真正的边界是 GWL_HTTP_AUTH_TOKEN（Config 已经强制要求）。
        out.append("*")
    # 兼容不带端口的 Host 头（HTTP/1.0 代理）
    for name in list(_LOOPBACK_HOSTS):
        out.append("%s:%d" % (name, config.http_port))
    return list(dict.fromkeys(out))


def _allowed_origins(config: Config) -> list[str]:
    """浏览器侧允许的来源：配置值 + 本机常用端口（含 MCP Inspector 的 6274）。

    注意：SDK 的 allowed_origins 是精确匹配（不分端口通配），因此这里显式列举；
    非浏览器客户端不带 Origin，天然不受影响。
    """
    defaults = [
        "http://localhost:3000", "http://127.0.0.1:3000",
        "http://localhost:8765", "http://127.0.0.1:8765",
        "http://localhost:6274", "http://127.0.0.1:6274",
        "http://localhost:6277", "http://127.0.0.1:6277",
    ]
    return list(dict.fromkeys(list(config.http_allowed_origins) + defaults))