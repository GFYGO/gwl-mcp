"""MCP 资源：把「静态目录」与「常用只读上游数据」暴露成可浏览的 URI。

为什么需要资源而不只是工具：

* gwl://tools/inventory 与 gwl://api/overview 让客户端/模型 **不用调用任何上游接口**
  就能拿到「工具 ↔ HTTP 端点」对照表与限流口径，排查问题极快；
* gwl://me、gwl://documents/by-id/{doc_id} 这类读资源语义清晰（可缓存、可订阅），
  比让模型每次自己拼 gwl_get_document_by_id 更稳。

资源一律只读；上游失败按 MCP 语义映射：404 → ResourceNotFoundError（-32602），
其它错误 → ResourceError（-32603，消息可读、不含凭据）。
"""
from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver.exceptions import ResourceError, ResourceNotFoundError

from .api import ApiClient
from .errors import UpstreamError
from .registry import ordered
from .runtime import Runtime


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False)


async def _read_api(runtime: Runtime, method: str, path: str, *, not_found: str) -> str:
    """资源读取的统一实现：调用上游并把错误翻译成 MCP 资源错误。"""
    try:
        response = await runtime.api.arequest(method, path)
    except UpstreamError as exc:
        if exc.status == 404:
            raise ResourceNotFoundError(not_found + "（上游 404）") from exc
        raise ResourceError(exc.message) from exc
    return _json(response.envelope())


def register(server: Any, runtime: Runtime) -> None:
    """把所有资源挂到 MCPServer 上。

    官方 SDK 的限制：**静态资源**的 handler 不能声明 Context 参数
    （只有 URI 里带模板变量的资源才支持注入）。因此这里统一用闭包捕获
    lifespan 之外就已构造好的 Runtime，工具与资源共用同一个实例。
    """

    @server.resource(
        "gwl://api/overview",
        name="api-overview",
        title="服务与上游 API 概览",
        description="GWL /api/v1 的基址、鉴权方式、限流口径、超时与重试策略、写保护状态（不含任何密钥）。",
        mime_type="application/json",
    )
    async def overview() -> str:
        from .tools import names

        return _json({
            "service": {"name": "gwl-mcp", "version": _version(), "transport_note": "stdio 与 Streamable HTTP 共用同一套工具"},
            "upstream": {
                "base_url": runtime.config.base_url,
                "auth": "userid + apikey（请求头 X-User-Id / X-Api-Key，值来自 GWL_USER_ID / GWL_API_KEY）",
                "org_class_identity": runtime.config.org_class,
                "envelope": "{code, msg, data}；code 恒等于 HTTP 状态码，200 为成功",
            },
            "limits": {
                "rate_limit_per_minute": {
                    "read": 120, "write": 30, "ip": 240, "auth_fail": 20,
                    "note": "上游进程内 60 秒滑动窗口，多 worker 各算一份；以 gwl_ping 返回的 rate_limit 为准",
                },
                "http": {"timeout_seconds": runtime.config.timeout,
                         "total_timeout_seconds": runtime.config.total_timeout,
                         "max_retries": runtime.config.max_retries},
                "body": "上游 MAX_CONTENT_LENGTH=16MB，超出 413",
            },
            "retry_policy": {
                "429": "任何方法都按 Retry-After（夹在 1~30 秒）重试",
                "5xx_or_network": "只有 GET 重试（POST/PUT/DELETE 不重放，避免重复建文档 / 假 409）",
            },
            "safety": {
                "enable_writes": runtime.config.enable_writes,
                "permanent_delete_exposed": False,
                "note": "GWL_ENABLE_WRITES=0 时所有写工具直接报错；永久删除接口刻意不封装成工具",
            },
            "counts": {"tools": len(names()), "resources": 7},
        })

    @server.resource(
        "gwl://tools/inventory",
        name="tool-inventory",
        title="工具与 HTTP 端点对照表",
        description="每个 MCP 工具对应的上游方法/路径、读写属性与参数列表；用于核对能力覆盖。",
        mime_type="application/json",
    )
    async def inventory() -> str:
        from .registry import REGISTRY

        rows = []

        for spec in ordered(REGISTRY):
            meta = runtime.index.get(spec.name)
            rows.append({
                "tool": spec.name,
                "title": spec.title,
                "endpoint": spec.endpoint,
                "read_only": spec.read_only,
                "destructive": spec.destructive,
                "idempotent": spec.idempotent,
                "parameters": list(meta.properties) if meta else [],
            })
        return _json({"tools": rows, "total": len(rows)})

    @server.resource(
        "gwl://me",
        name="me",
        title="我的资料",
        description="当前 API Key 所属用户的资料（等价于 GET /api/v1/me）。",
        mime_type="application/json",
    )
    async def me() -> str:
        return await _read_api(runtime, "GET", "/me", not_found="用户不存在")

    @server.resource(
        "gwl://permissions",
        name="permissions",
        title="我的实时权限",
        description="当前请求的实时权限快照（等价于 GET /api/v1/permissions）。",
        mime_type="application/json",
    )
    async def permissions() -> str:
        return await _read_api(runtime, "GET", "/permissions", not_found="权限快照不可用")

    @server.resource(
        "gwl://documents/by-id/{doc_id}",
        name="document-by-id",
        title="文档正文（按 id）",
        description="按文档 id 读取全文；不会增加浏览数（等价于 GET /api/v1/documents/id/<doc_id>）。",
        mime_type="application/json",
    )
    async def document_by_id(doc_id: str) -> str:
        from .registry import seg

        return await _read_api(runtime, "GET", "/documents/id/" + seg(doc_id),
                               not_found="文档 %s 不存在或对你不可见" % doc_id)

    @server.resource(
        "gwl://documents/by-slug/{slug}",
        name="document-by-slug",
        title="文档正文（按 slug）",
        description="按 slug 读取全文。⚠️ 与工具 gwl_get_document 一样会增加 view_count，例行读取请用 by-id。",
        mime_type="application/json",
    )
    async def document_by_slug(slug: str) -> str:
        from .registry import seg

        return await _read_api(runtime, "GET", "/documents/" + seg(slug),
                               not_found="slug 为 %s 的文档不存在或对你不可见" % slug)

    @server.resource(
        "gwl://notifications",
        name="notifications",
        title="我的通知（默认前 50 条）",
        description="个人通知 + 可见的全局通知 + 未读计数（等价于 GET /api/v1/notifications?limit=50）。",
        mime_type="application/json",
    )
    async def notifications() -> str:
        return await _read_api(runtime, "GET", "/notifications?limit=50", not_found="通知不可读")


def _version() -> str:
    from . import __version__

    return __version__