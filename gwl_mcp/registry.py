"""工具注册表：把「一个 HTTP 方法 + 一条路径」映射成一个 MCP 工具。

设计原则（沿用线上 /mcp 端点与 v0 已经验收过的口径）：

* 名称一律 gwl_<动词>_<对象>，描述里写清权限要求与副作用；
* 每个工具用 meta 声明它对应的上游端点（方法 + 路径模板），
  runtime 与 gwl://tools/inventory 资源都从这份声明生成，不另建表，杜绝两处漂移；
* 只读工具 readOnlyHint=true；写工具 false；删除类 destructiveHint=true；
* 永久删除（DELETE /documents/id/<id>/permanent）刻意不封装成工具，
  且没有任何开关能打开它 —— 与 site-back/services/mcp/tools.py 的决策一致。
"""
from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .errors import GwlError
from .runtime import ENDPOINT_META_KEY, error_text

#: 所有被 gwl_tool 装饰过的函数都会在导入期登记到这里
REGISTRY: list[ToolSpec] = []


@dataclass(frozen=True)
class ToolSpec:
    name: str
    title: str
    description: str
    endpoint: str
    read_only: bool
    destructive: bool
    idempotent: bool
    fn: Callable[..., Any] = field(compare=False, repr=False)


def gwl_tool(
    *,
    name: str,
    title: str,
    endpoint: str,
    description: str,
    read_only: bool = True,
    destructive: bool = False,
    idempotent: bool | None = None,
):
    """声明一个 MCP 工具；真正的注册发生在 install()。

    idempotent 默认：只读工具 True；写工具里 PUT / DELETE（上游幂等语义）True，
    POST False（重复 POST /documents 会真的多出一篇）。
    """
    if idempotent is None:
        method = endpoint.split(" ", 1)[0].upper()
        idempotent = read_only or method in ("PUT", "DELETE")

    def decorator(fn):
        if not inspect.iscoroutinefunction(fn):
            raise TypeError("工具函数必须是 async def：" + name)
        REGISTRY.append(
            ToolSpec(
                name=name,
                title=title,
                description=description.strip(),
                endpoint=endpoint,
                read_only=read_only,
                destructive=destructive,
                idempotent=bool(idempotent),
                fn=fn,
            )
        )
        return fn

    return decorator


def install(server, *, skip: set[str] | None = None) -> list[ToolSpec]:
    """把已声明的工具注册进官方 SDK 的 MCPServer。"""
    skip = skip or set()
    installed: list[ToolSpec] = []
    for spec in ordered(REGISTRY):
        if spec.name in skip:
            continue
        server.add_tool(
            _guarded(spec.fn),
            name=spec.name,
            title=spec.title,
            description=spec.description,
            annotations=ToolAnnotations(
                title=spec.title,
                readOnlyHint=spec.read_only,
                destructiveHint=spec.destructive,
                idempotentHint=spec.idempotent,
                openWorldHint=True,
            ),
            meta={ENDPOINT_META_KEY: spec.endpoint},
        )
        installed.append(spec)
    return installed


def _guarded(fn):
    """把工具函数抛出的可预期错误翻译成 MCP 的 ToolError（isError=true + 可读消息）。

    不包一层的话，GwlError 会被官方 SDK 判成「未预期崩溃」：
    客户端只看到 "Error executing tool xxx"，我们的中文原因与结构化字段全被吞掉。
    functools.wraps 保留原签名（含 Context 参数），因此 inputSchema 生成不受影响。
    """

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except ToolError:
            raise
        except GwlError as exc:
            raise ToolError(error_text(exc)) from exc

    return wrapper


def ordered(specs):
    """按 _ORDER 排序；没登记顺序的排在最后（测试会因此报警）。"""
    return sorted(specs, key=lambda item: (_ORDER.index(item.name) if item.name in _ORDER else 999, item.name))


def seg(value) -> str:
    """URL 路径段：严禁把用户输入原样拼进路径。"""
    return quote(str(value), safe="")


def lower_cid(value: str) -> str:
    return str(value).lower()


def types_csv(items: list[str] | None) -> str | None:
    """搜索类型：上游只认 discover，用户侧文档写的是 discover_item。"""
    if not items:
        return None
    out: list[str] = []
    for item in items:
        key = "discover" if item == "discover_item" else item
        if key not in out:
            out.append(key)
    return ",".join(out) or None


def tags_csv(items: list[str] | None) -> str | None:
    if not items:
        return None
    return ",".join(items)


async def call(ctx, tool_name: str, **kwargs):
    """工具函数的统一出口（见 runtime.Runtime.call）。"""
    from .runtime import runtime_of

    return await runtime_of(ctx).call(ctx, tool_name, **kwargs)


#: 工具顺序：与线上 /mcp 的 tools/list 顺序一致，便于客户端迁移时逐项比对
_ORDER = [
    "gwl_ping", "gwl_get_me", "gwl_get_permissions",
    "gwl_list_my_classes", "gwl_list_my_orgs", "gwl_get_class", "gwl_list_joinable_classes",
    "gwl_get_join_info", "gwl_join_class", "gwl_list_my_join_requests", "gwl_cancel_join_request",
    "gwl_list_class_join_requests", "gwl_review_join_request", "gwl_list_blacklist",
    "gwl_add_to_blacklist", "gwl_remove_from_blacklist",
    "gwl_list_folders", "gwl_list_documents", "gwl_create_document", "gwl_list_my_documents",
    "gwl_list_trash", "gwl_get_document", "gwl_get_document_by_id", "gwl_update_document",
    "gwl_delete_document", "gwl_list_document_revisions", "gwl_get_document_revision",
    "gwl_restore_document_revision", "gwl_restore_document",
    "gwl_search", "gwl_suggest_tags", "gwl_get_tag_facets", "gwl_get_object_tags", "gwl_set_object_tags",
    "gwl_list_notifications", "gwl_mark_notifications_read",
]


def names() -> list[str]:
    return [spec.name for spec in ordered(REGISTRY)]