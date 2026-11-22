"""身份与自检类工具（对应 /ping、/me、/permissions）。"""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from mcp.server.mcpserver.context import Context

from ..registry import call, gwl_tool

_CREDENTIAL_HINT = "凭据（GWL_USER_ID / GWL_API_KEY）来自环境变量，工具参数里不需要也不能传 userid/apikey。"


@gwl_tool(
    name="gwl_ping",
    title="连通性与凭据自检",
    endpoint="GET /ping",
    description=(
        "验证当前 userid + apikey 是否可用，并返回身份信息与本进程生效的限流口径。\n"
        + _CREDENTIAL_HINT + "\n"
        "建议每次会话开始时先调用一次：它同时告诉你上游的读/写/IP 配额，"
        "便于安排后续调用节奏。返回 data 含 userid、username、real_level（1~5）、"
        "server_time（UTC ISO）、rate_limit{read_per_minute,write_per_minute,ip_per_minute,auth_fail_per_minute}。"
    ),
)
async def gwl_ping(ctx: Context) -> dict[str, Any]:
    return await call(ctx, "gwl_ping")


@gwl_tool(
    name="gwl_get_me",
    title="我的资料",
    endpoint="GET /me",
    description=(
        "读取当前 API Key 所属用户的个人资料（与前端「个人中心」同源）。\n"
        "返回 id、username、nickname、display_name、email、phone、avatar、theme、"
        "introduction、gender、location、group_id/group_name、slug、is_public、workspace_layout 等。\n"
        "出于安全，永远不含 account_apikey、password_hash、pin；要拿权限等级请用 gwl_get_permissions。"
    ),
)
async def gwl_get_me(ctx: Context) -> dict[str, Any]:
    return await call(ctx, "gwl_get_me")


@gwl_tool(
    name="gwl_get_permissions",
    title="实时权限快照",
    endpoint="GET /permissions",
    description=(
        "返回本次请求实时解析出的权限（服务端每次请求重算，不信任任何客户端声明）。\n"
        "字段：userid、real_level、level、context（apikey 路径恒为 null）、"
        "nodes（5 段权限节点串列表）、class_id 与 class_role（当前组类身份）。\n"
        "想以某个组类身份操作，用环境变量 GWL_ORG_CLASS（或请求级 X-Org-Class 语义），"
        "不属于该组类时会被静默忽略。"
    ),
)
async def gwl_get_permissions(ctx: Context) -> dict[str, Any]:
    return await call(ctx, "gwl_get_permissions")
