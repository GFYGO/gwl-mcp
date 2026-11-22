"""通知域：2 个工具（/notifications、/notifications/read）。"""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from mcp.server.mcpserver.context import Context

from ..errors import ValidationError
from ..params import NotificationLimit
from ..registry import call, gwl_tool


@gwl_tool(
    name="gwl_list_notifications",
    title="我的通知",
    endpoint="GET /notifications",
    description=(
        "返回我的站内通知 + 我当前可见的全局通知 + 未读计数。\n"
        "结构：{personal[], global[]（带 is_read）, unread{total,global,personal}}。\n"
        "这不是分页包络，用 limit 截断（默认 100，最大 200）。"
    ),
)
async def gwl_list_notifications(ctx: Context, limit: NotificationLimit = 100) -> dict[str, Any]:
    return await call(ctx, "gwl_list_notifications", query={"limit": limit})


@gwl_tool(
    name="gwl_mark_notifications_read",
    title="标记通知已读",
    endpoint="POST /notifications/read",
    read_only=False,
    description=(
        "把指定通知标记为已读，返回新的未读计数 {unread{total,global,personal}}。\n"
        "global_ids 与 personal_ids 至少要给一个且不能是空列表，否则本工具直接报错。\n"
        "只能标记自己的通知；别人的 id 会被上游静默忽略。操作幂等（重复调用结果一致）。"
    ),
)
async def gwl_mark_notifications_read(
    ctx: Context,
    global_ids: Annotated[list[Annotated[int, Field(ge=1)]] | None, Field(
        description="要标记已读的全局通知 id（最多 500 个，超出上游会截断）。", max_length=500)] = None,
    personal_ids: Annotated[list[Annotated[int, Field(ge=1)]] | None, Field(
        description="要标记已读的个人通知 id（最多 500 个，超出上游会截断）。", max_length=500)] = None,
) -> dict[str, Any]:
    if not global_ids and not personal_ids:
        raise ValidationError(
            "gwl_mark_notifications_read 需要至少一个非空 id 列表：global_ids 或 personal_ids。",
            detail={"hint": "先用 gwl_list_notifications 取 id。"},
        )
    return await call(ctx, "gwl_mark_notifications_read",
                      body={"global_ids": global_ids, "personal_ids": personal_ids})
