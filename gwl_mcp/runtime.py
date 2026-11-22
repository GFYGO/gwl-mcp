"""工具运行时：所有 MCP 工具调用都经过这里的一根管线。

职责（对应交付要求里的鉴权 / 超时 / 重试 / 参数校验 / 错误返回 / 日志）：

* **鉴权**：由 ApiClient 用请求头注入 X-User-Id / X-Api-Key（值只来自环境变量）；
* **超时 / 重试**：交给 ApiClient（单次 timeout + 总预算 + 429/GET 退避）；
* **参数校验**：官方 SDK 先按生成的 JSON Schema 做类型/必填/枚举/范围校验；
  这里再补 SDK 不管的三件事 —— 未知参数拒绝、跨字段约束、只读开关；
* **错误返回**：可预期错误统一转成 MCP 的 isError=true（模型可读），
  而不是 JSON-RPC 协议错误；协议错误只留给协议层；
* **日志**：每个工具调用一行结构化日志，走 stderr，不含凭据也不含参数值。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from mcp.server.mcpserver.context import Context
from mcp.server.mcpserver.exceptions import ToolError

from .api import ApiClient, ApiResponse
from .config import Config
from .errors import ConfigError, GwlError, UpstreamError, ValidationError
from .log import log_tool_call

LOG = logging.getLogger("gwl_mcp.runtime")

#: 每个工具在注册时通过 meta 声明上游端点，供索引与资源目录使用
ENDPOINT_META_KEY = "gwl/upstream-endpoint"


@dataclass(frozen=True)
class ToolMeta:
    """一个 MCP 工具与上游 HTTP 端点的对应关系。"""

    name: str
    endpoint: str
    read_only: bool
    destructive: bool
    idempotent: bool
    properties: tuple[str, ...]
    description: str = ""

    @property
    def method(self) -> str:
        return self.endpoint.split(" ", 1)[0]

    @property
    def path(self) -> str:
        return self.endpoint.split(" ", 1)[1] if " " in self.endpoint else ""


@dataclass
class Runtime:
    """一次进程生命周期内共享的状态（由 lifespan 创建）。"""

    config: Config
    api: ApiClient
    index: dict[str, ToolMeta] = field(default_factory=dict)

    def build_index(self, tools: list[Any]) -> None:
        """从 SDK 已注册的工具对象构建索引。

        properties 直接取 SDK 生成的 inputSchema，因此「下发给客户端的 schema」
        与「服务端实际校验的字段集合」永远是同一份，不会漂移。
        """
        index: dict[str, ToolMeta] = {}
        for tool in tools:
            meta = getattr(tool, "meta", None) or {}
            annotations = getattr(tool, "annotations", None)
            schema = getattr(tool, "input_schema", None) or {}
            properties = tuple((schema.get("properties") or {}).keys())
            name = tool.name
            endpoint = str(meta.get(ENDPOINT_META_KEY) or "")
            read_only = bool(getattr(annotations, "read_only_hint", False)) if annotations else False
            destructive = bool(getattr(annotations, "destructive_hint", False)) if annotations else False
            idempotent = bool(getattr(annotations, "idempotent_hint", True)) if annotations else True
            index[name] = ToolMeta(
                name=name,
                endpoint=endpoint,
                read_only=read_only,
                destructive=destructive,
                idempotent=idempotent,
                properties=properties,
                description=str(getattr(tool, "description", "") or ""),
            )
        self.index = index

    def meta(self, tool_name: str) -> ToolMeta:
        found = self.index.get(tool_name)
        if found is None:  # 注册遗漏：属于开发期错误，直接抛出让测试抓到
            raise ConfigError("内部错误：工具 %s 没有建立索引（注册遗漏）" % tool_name)
        return found

    # ------------------------------------------------------------------
    # 校验
    # ------------------------------------------------------------------
    def reject_unknown_arguments(self, ctx: Context, tool_name: str, consumed: dict[str, Any]) -> None:
        """拒绝未知参数。

        SDK 只会忽略它不认识的键；本服务把它当成错误（与线上 /mcp 端点一致）：
        模型拼错参数名时，静默丢弃会让它以为参数生效了。
        """
        raw = _raw_arguments(ctx)
        if raw is None:
            return
        allowed = set(self.meta(tool_name).properties)
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise ValidationError(
                "未知参数 %s；%s 可用参数：%s"
                % (
                    "、".join(repr(k) for k in unknown),
                    tool_name,
                    "、".join(sorted(allowed)) or "（无）",
                ),
                detail={"unknown": unknown, "allowed": sorted(allowed)},
            )

    def ensure_writes_allowed(self, tool_name: str) -> None:
        meta = self.meta(tool_name)
        if meta.read_only or self.config.enable_writes:
            return
        raise ConfigError(
            "写操作已被禁用：当前 GWL_ENABLE_WRITES=0。"
            "确实需要创建/修改/删除时，把环境变量改成 1 并重启 MCP 服务。"
            "（被拦下的调用：%s，对应上游 %s）" % (tool_name, meta.endpoint),
        )

    # ------------------------------------------------------------------
    # 调用
    # ------------------------------------------------------------------
    async def call(
        self,
        ctx: Context,
        tool_name: str,
        *,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        path: str | None = None,
    ) -> dict[str, Any]:
        """执行一次上游调用，返回给 MCP 的结构化结果。

        path 省略时用注册表里的端点路径（无路径参数的工具）。
        """
        meta = self.meta(tool_name)
        self.reject_unknown_arguments(ctx, tool_name, {})
        self.ensure_writes_allowed(tool_name)

        target = path if path is not None else meta.path
        method = meta.method
        try:
            response: ApiResponse = await self.api.arequest(
                method, target, query=query, body=_compact(body) if body is not None else None
            )
        except UpstreamError as exc:
            log_tool_call(
                LOG, tool_name, meta.endpoint, ok=False,
                status=exc.status, attempts=exc.attempts, elapsed_ms=0, msg=exc.message,
            )
            raise ToolError(error_text(exc)) from exc
        except GwlError as exc:
            log_tool_call(LOG, tool_name, meta.endpoint, ok=False, status=None,
                          attempts=1, elapsed_ms=0, msg=exc.message)
            raise ToolError(_error_text(exc)) from exc

        log_tool_call(
            LOG, tool_name, meta.endpoint, ok=True, status=response.status,
            attempts=response.attempts, elapsed_ms=response.elapsed_ms, msg=response.msg,
        )
        # 说明：不用 ctx.log() 发 MCP logging/message 通知 —— 该能力在协议 2026-07-28
        # 里已被标记废弃（SEP-2577）。日志统一走 stderr，客户端需要时用 setLevel 无关。
        return response.envelope()


def error_text(exc: GwlError) -> str:
    """错误首行给人看，后面的 JSON 给模型看（保持 isError=true 语义可读）。"""
    payload = exc.to_payload()
    machine = {k: v for k, v in payload.items() if k not in ("ok", "message", "detail")}
    if machine or payload.get("detail") is not None:
        extra = dict(machine)
        if payload.get("detail") is not None:
            extra["detail"] = payload["detail"]
        return "%s\n%s" % (payload["message"], json.dumps(extra, ensure_ascii=False, sort_keys=True))
    return payload["message"]


def _compact(body: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in body.items() if value is not None}


def _raw_arguments(ctx: Context) -> dict[str, Any] | None:
    """从请求上下文里取未经 SDK 过滤的原始 arguments。

    协议 2026-07-28 下 request_context.params 是 dict
    （name / arguments / _meta）；旧协议路径里它可能是带 arguments 属性的模型。
    两种形状都要能吃，否则未知参数检查会静默失效。
    """
    request_context = getattr(ctx, "request_context", None)
    params = getattr(request_context, "params", None)
    if isinstance(params, dict):
        arguments = params.get("arguments")
    else:
        arguments = getattr(params, "arguments", None)
    if isinstance(arguments, dict):
        return arguments
    return None


def runtime_of(ctx: Context) -> Runtime:
    """从 Context 取出 lifespan 创建的 Runtime。"""
    holder = getattr(ctx, "request_context", None)
    lifespan = getattr(holder, "lifespan_context", None)
    if isinstance(lifespan, Runtime):
        return lifespan
    # 直接调用 MCPServer.call_tool() 的单测路径（未走 lifespan）会到这里
    raise ConfigError("内部错误：Runtime 未初始化（请通过 run.py / 测试夹具启动服务）")