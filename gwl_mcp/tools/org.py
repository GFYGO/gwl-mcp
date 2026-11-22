"""组织 / 组类域：13 个工具（对应 /api/v1 的 /org/* 路由）。

权限语义全部来自 site-back/services/org_service.py 与 org_join_service.py，
不是文档转述：403=不是成员/不是管理者/在黑名单/组已停用，409=已处理或已在黑名单。
"""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from mcp.server.mcpserver.context import Context

from ..params import CID_PATTERN, JoinRequestStatus, Note, RequestId, Role, Username, Cid
from ..registry import call, gwl_tool, lower_cid, seg

_MANAGE = "需要该组类的管理角色（class_role=owner 或权限含 manage），否则 403。"


@gwl_tool(
    name="gwl_list_my_classes",
    title="我的组类清单",
    endpoint="GET /org/classes",
    description=(
        "平铺返回我加入或拥有的所有组类（组织 + 组），是找 cid 的入口。\n"
        "每项含 id（3 位字母）、name、kind（organization/group）、org_id、owner、role、"
        "is_active、is_public、node、can_manage。\n"
        "另带 status{tables_ready, missing, hint}：组织数据表未就绪时 hint 会说明原因。"
    ),
)
async def gwl_list_my_classes(ctx: Context) -> dict[str, Any]:
    return await call(ctx, "gwl_list_my_classes")


@gwl_tool(
    name="gwl_list_my_orgs",
    title="我的组织树",
    endpoint="GET /org/orgs",
    description=(
        "返回我加入/拥有的组织，组织为顶层、下辖我可见的组（groups 数组）。\n"
        "适合先看清层级关系，再决定用哪个 cid 去调用组类详情或黑名单类工具。"
    ),
)
async def gwl_list_my_orgs(ctx: Context) -> dict[str, Any]:
    return await call(ctx, "gwl_list_my_orgs")


@gwl_tool(
    name="gwl_get_class",
    title="组类详情",
    endpoint="GET /org/classes/{cid}",
    description=(
        "读取某个组类的详细信息：id、name、kind、org_id、owner、role、can_manage、"
        "permissions[]、permission_map。\n"
        "仅成员、owner、上级组织管理方或超管可见；不是成员 403，不存在 404，"
        "cid 不是 3 位字母 400。"
    ),
)
async def gwl_get_class(
    ctx: Context,
    cid: Annotated[Cid, Field(description="组类标识（路径参数）。")],
) -> dict[str, Any]:
    return await call(ctx, "gwl_get_class", path="/org/classes/" + seg(lower_cid(cid)))


@gwl_tool(
    name="gwl_list_joinable_classes",
    title="可加入的公开组",
    endpoint="GET /org/joinable",
    description=(
        "列出所有允许自助加入的 public 组（返回裸数组，上限 200 条）。\n"
        "class_id 可收窄到某个组织下的组；传了不属于任何组织的 id 会 400。"
    ),
)
async def gwl_list_joinable_classes(
    ctx: Context,
    class_id: Annotated[
        str | None,
        Field(description="可选：只看某个组织/组类下的可加入组（3 位字母）。省略=全部公开组。",
              pattern=CID_PATTERN),
    ] = None,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if class_id:
        query["class_id"] = lower_cid(class_id)
    return await call(ctx, "gwl_list_joinable_classes", query=query)


@gwl_tool(
    name="gwl_get_join_info",
    title="组的加入状态",
    endpoint="GET /org/classes/{cid}/join-info",
    description=(
        "查询某组类的可见性与「我」的状态：is_public、joinable、is_member、blacklisted、"
        "request（我提交的待处理申请，没有则 null）。\n"
        "这条不需要组类身份，任何人都能先探测再决定要不要申请加入。"
    ),
)
async def gwl_get_join_info(
    ctx: Context,
    cid: Annotated[Cid, Field(description="目标组类标识（路径参数）。")],
) -> dict[str, Any]:
    return await call(ctx, "gwl_get_join_info", path="/org/classes/" + seg(lower_cid(cid)) + "/join-info")


@gwl_tool(
    name="gwl_join_class",
    title="加入组类",
    endpoint="POST /org/classes/{cid}/join",
    read_only=False,
    description=(
        "加入组类：public 组直接入组（data.mode=joined），private 组转为提交申请"
        "（data.mode=requested）。\n"
        "已经是成员不会报错，会回 already_member=true（幂等）。\n"
        "失败语义：组已停用 / 非公开且不允许申请 / 我在黑名单 403；"
        "cid 非法 400。message 最多 200 个字符。"
    ),
)
async def gwl_join_class(
    ctx: Context,
    cid: Annotated[Cid, Field(description="要加入的组类标识（路径参数）。")],
    role: Role = "member",
    message: Annotated[str, Field(description="申请附言（private 组会给审批人看），最多 200 个字符。",
                                 max_length=200)] = "",
) -> dict[str, Any]:
    return await call(
        ctx,
        "gwl_join_class",
        path="/org/classes/" + seg(lower_cid(cid)) + "/join",
        body={"role": role, "message": message},
    )


@gwl_tool(
    name="gwl_list_my_join_requests",
    title="我提交的申请",
    endpoint="GET /org/join-requests/mine",
    description=(
        "列出我提交过的加入申请（裸数组）。默认只返回待处理（pending）的；\n"
        "handled=true 时连已批准/已拒绝/已撤回的一起返回。\n"
        "每项含 group、requester_username、reviewer_username、status、already_member。"
    ),
)
async def gwl_list_my_join_requests(
    ctx: Context,
    handled: Annotated[bool, Field(description="true=同时返回已处理的申请；默认 false 只看待处理。")] = False,
) -> dict[str, Any]:
    return await call(ctx, "gwl_list_my_join_requests", query={"handled": 1 if handled else 0})


@gwl_tool(
    name="gwl_cancel_join_request",
    title="撤回我的申请",
    endpoint="DELETE /org/join-requests/{request_id}",
    read_only=False,
    destructive=True,
    description=(
        "撤回我自己提交的加入申请，状态变为 cancelled。\n"
        "只能撤自己的（否则 403）；已被批准或拒绝过的再撤回会 409。\n"
        "先用 gwl_list_my_join_requests 取 request_id。"
    ),
)
async def gwl_cancel_join_request(
    ctx: Context,
    request_id: Annotated[RequestId, Field(description="申请 id（路径参数）。")],
) -> dict[str, Any]:
    return await call(ctx, "gwl_cancel_join_request", path="/org/join-requests/" + seg(request_id))


@gwl_tool(
    name="gwl_list_class_join_requests",
    title="组收到的申请",
    endpoint="GET /org/classes/{cid}/join-requests",
    description=(
        "列出某组类收到的加入申请（裸数组，需管理权限）。\n"
        "传组织 id 会汇总其下辖组的申请。status 默认 pending，"
        "可取 all / approved / rejected。" + _MANAGE
    ),
)
async def gwl_list_class_join_requests(
    ctx: Context,
    cid: Annotated[Cid, Field(description="组类或组织标识（路径参数）。")],
    status: Annotated[JoinRequestStatus, Field(
        description="筛选申请状态。默认 pending（待处理）；all 返回全部历史。")] = "pending",
) -> dict[str, Any]:
    return await call(
        ctx, "gwl_list_class_join_requests",
        path="/org/classes/" + seg(lower_cid(cid)) + "/join-requests",
        query={"status": status},
    )


@gwl_tool(
    name="gwl_review_join_request",
    title="审批加入申请",
    endpoint="POST /org/join-requests/{request_id}/review",
    read_only=False,
    description=(
        "批准或拒绝一条加入申请：approve=true 批准入组，false 拒绝。\n"
        "批准只是建立成员关系，不会因此提权。\n"
        "已处理过的再审批 409；目标用户在黑名单里 409；申请不存在 404。" + _MANAGE
    ),
)
async def gwl_review_join_request(
    ctx: Context,
    request_id: Annotated[RequestId, Field(description="申请 id（路径参数）。")],
    approve: Annotated[bool, Field(description="true=批准入组，false=拒绝。默认 true。")] = True,
    note: Note = "",
) -> dict[str, Any]:
    return await call(
        ctx, "gwl_review_join_request",
        path="/org/join-requests/" + seg(request_id) + "/review",
        body={"approve": approve, "note": note},
    )


@gwl_tool(
    name="gwl_list_blacklist",
    title="组类黑名单",
    endpoint="GET /org/classes/{cid}/blacklist",
    description=(
        "列出该组类的黑名单（裸数组：user_id、username、reason、created_by、created_at）。\n"
        "传组织 id 时汇总下辖组。" + _MANAGE
    ),
)
async def gwl_list_blacklist(
    ctx: Context,
    cid: Annotated[Cid, Field(description="组类或组织标识（路径参数）。")],
) -> dict[str, Any]:
    return await call(ctx, "gwl_list_blacklist", path="/org/classes/" + seg(lower_cid(cid)) + "/blacklist")


@gwl_tool(
    name="gwl_add_to_blacklist",
    title="拉黑用户",
    endpoint="POST /org/classes/{cid}/blacklist",
    read_only=False,
    description=(
        "把一个用户加入组类黑名单（会同时给对方发一条站内通知）。\n"
        "拉黑自己 400；已在名单里 409。" + _MANAGE
    ),
)
async def gwl_add_to_blacklist(
    ctx: Context,
    cid: Annotated[Cid, Field(description="组类标识（路径参数）。")],
    username: Annotated[Username, Field(description="要拉黑的用户登录名。")],
    reason: Note = "",
) -> dict[str, Any]:
    return await call(
        ctx, "gwl_add_to_blacklist",
        path="/org/classes/" + seg(lower_cid(cid)) + "/blacklist",
        body={"username": username, "reason": reason},
    )


@gwl_tool(
    name="gwl_remove_from_blacklist",
    title="解除拉黑",
    endpoint="DELETE /org/classes/{cid}/blacklist",
    read_only=False,
    description=(
        "把一个用户移出组类黑名单（同样会通知对方）。\n"
        "该用户不在名单里时返回 404；重复调用不幂等。" + _MANAGE
    ),
)
async def gwl_remove_from_blacklist(
    ctx: Context,
    cid: Annotated[Cid, Field(description="组类标识（路径参数）。")],
    username: Annotated[Username, Field(description="要解除拉黑的用户登录名。")],
) -> dict[str, Any]:
    return await call(
        ctx, "gwl_remove_from_blacklist",
        path="/org/classes/" + seg(lower_cid(cid)) + "/blacklist",
        body={"username": username},
    )
