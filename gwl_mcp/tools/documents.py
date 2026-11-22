"""文件夹 / 文档域：13 个工具（对应 /api/v1 的 /folders 与 /documents 路由）。

上游语义要点（都已在 site-back/services/document/ 核对）：
* 列表恒返回分页包络 {items,page,size,total,has_more}，语义是「先判权再切片」，
  越界页返回空数组而不是 404；
* folder_id：0=根目录，-1=全部文件夹（不分层），正整数=指定文件夹；
* 更新走乐观锁：data.version（或 HTTP If-Match，本工具只支持 data.version），
  冲突返回 409 且 data 里带 current_version / expected_version / editor_username；
* 软删除可恢复，因此只有它是 destructive；永久删除刻意不封装。
"""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from mcp.server.mcpserver.context import Context

from ..errors import ValidationError
from ..params import (
    Content,
    DocId,
    FolderId,
    Icon,
    NotificationLimit,
    Owning,
    Page,
    ParentFolderId,
    PermissionBits,
    Rev,
    Scope,
    Size,
    Slug,
    Summary,
    TagList,
    TagMode,
    Title,
    UserId,
    Visibility,
)
from ..registry import call, gwl_tool, seg, tags_csv

_MUTABLE = ("title", "content", "summary", "icon", "folder_id", "permission_bits", "visibility", "owning")


@gwl_tool(
    name="gwl_list_folders",
    title="文件夹列表",
    endpoint="GET /folders",
    description=(
        "只列文件夹（不含文档），返回裸数组：id、name、scope、user_id、group_name、"
        "parent_id、sort_order、is_active、created_at、updated_at；group 范围的项还带 class_id/org_id/group_root。\n"
        "scope 省略时返回我全部可见的文件夹；parent_id=0 只取根级，正整数只取该文件夹的直接子级，"
        "省略则返回全部层级。"
    ),
)
async def gwl_list_folders(
    ctx: Context,
    scope: Annotated[Scope | None, Field(description="文档范围；省略=我可见的全部范围。")] = None,
    parent_id: Annotated[ParentFolderId | None, Field(description="父文件夹 id：0=根级，正整数=该文件夹的直接子级，省略=全部层级。")] = None,
) -> dict[str, Any]:
    return await call(ctx, "gwl_list_folders", query={"scope": scope, "parent_id": parent_id})


@gwl_tool(
    name="gwl_list_documents",
    title="文档列表（分页）",
    endpoint="GET /documents",
    description=(
        "按范围列出文档元信息（不含正文），逐条判权后分页。\n"
        "返回 {items,page,size,total,has_more}；items 是 meta 视图，取正文用 "
        "gwl_get_document_by_id（不加浏览数）或 gwl_get_document（会 +1 浏览数）。\n"
        "folder_id：0=根目录（默认）、-1=该范围下全部文件夹、正整数=指定文件夹。\n"
        "deleted=1 只看回收站里的（需相应权限）；author_id 只在 scope=public 生效。\n"
        "tags 最多 8 个，tag_mode=all 表示同时命中，any 表示任一命中。"
    ),
)
async def gwl_list_documents(
    ctx: Context,
    scope: Annotated[Scope, Field(description="文档范围，默认 public。")] = "public",
    folder_id: Annotated[FolderId, Field(description="文件夹过滤：0=根目录，-1=全部文件夹，正整数=指定文件夹。")] = 0,
    deleted: Annotated[int | None, Field(description="0=只看未删除（默认），1=只看已软删除。非法值由上游回落。", ge=0, le=1)] = None,
    page: Page = 1,
    size: Size = 20,
    tags: Annotated[TagList | None, Field(description="标签过滤（最多 8 个），上游按逗号解析。")] = None,
    tag_mode: Annotated[TagMode | None, Field(description="标签命中方式：all=同时具备，any=任一具备。")] = None,
    author_id: Annotated[UserId | None, Field(description="只看某作者的文档；仅 scope=public 生效。")] = None,
) -> dict[str, Any]:
    return await call(
        ctx, "gwl_list_documents",
        query={
            "scope": scope,
            "folder_id": folder_id,
            "deleted": deleted,
            "page": page,
            "size": size,
            "tags": tags_csv(tags),
            "tag_mode": tag_mode,
            "author_id": author_id,
        },
    )


@gwl_tool(
    name="gwl_create_document",
    title="创建文档",
    endpoint="POST /documents",
    read_only=False,
    idempotent=False,
    description=(
        "创建一篇文档，返回文档详情（含 content，version=1）。\n"
        "title 必填非空；slug 省略时上游按毫秒时间戳自动生成，已占用则 400。\n"
        "写入范围（visibility/owning/folder_id/permission_bits）由服务端做写边界校验，"
        "越界返回 403；folder_id 不存在返回 400。\n"
        "本工具不幂等：网络失败时不要盲目重试，先用 gwl_list_my_documents 确认是否已经建过。"
    ),
)
async def gwl_create_document(
    ctx: Context,
    title: Title,
    content: Content = "",
    slug: Annotated[str | None, Field(description="自定义 slug（全站唯一）。省略则自动生成。", min_length=1, max_length=200)] = None,
    summary: Summary | None = None,
    icon: Icon | None = None,
    folder_id: Annotated[int | None, Field(description="归属文件夹 id。省略=根目录（0）。", ge=0)] = None,
    permission_bits: PermissionBits | None = None,
    visibility: Visibility | None = None,
    owning: Owning | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "title": title,
        "content": content,
        "slug": slug,
        "summary": summary,
        "icon": icon,
        "folder_id": folder_id,
        "permission_bits": permission_bits,
        "visibility": visibility,
        "owning": owning,
    }
    return await call(ctx, "gwl_create_document", body=body)


@gwl_tool(
    name="gwl_list_my_documents",
    title="我的文档",
    endpoint="GET /documents/mine",
    description=(
        "列出我自己的未删除文档（分页包络）。\n"
        "folder_id 默认 0（只看根目录）；要一次看全部请传 -1。"
    ),
)
async def gwl_list_my_documents(
    ctx: Context,
    folder_id: Annotated[FolderId, Field(description="0=根目录（默认），-1=全部文件夹，正整数=指定文件夹。")] = 0,
    page: Page = 1,
    size: Size = 20,
) -> dict[str, Any]:
    return await call(ctx, "gwl_list_my_documents",
                      query={"folder_id": folder_id, "page": page, "size": size})


@gwl_tool(
    name="gwl_list_trash",
    title="我的回收站",
    endpoint="GET /documents/trash",
    description=(
        "列出被我软删除的文档（分页包络，按删除时间倒序）。\n"
        "配合 gwl_restore_document 恢复；永久删除不提供工具。"
    ),
)
async def gwl_list_trash(ctx: Context, page: Page = 1, size: Size = 20) -> dict[str, Any]:
    return await call(ctx, "gwl_list_trash", query={"page": page, "size": size})


@gwl_tool(
    name="gwl_get_document",
    title="按 slug 读文档",
    endpoint="GET /documents/{slug}",
    idempotent=False,
    description=(
        "按 slug 读取文档全文（返回字段随可见性做权限位掩码）。\n"
        "⚠️ 有副作用：每次命中都会 view_count + 1，因此标记为非幂等。\n"
        "只是要读内容做编辑时，请改用 gwl_get_document_by_id（不加浏览数）。\n"
        "不存在 404；无权查看 403。"
    ),
)
async def gwl_get_document(
    ctx: Context,
    slug: Annotated[Slug, Field(description="文档 slug（路径参数，会自动做 URL 编码）。")],
) -> dict[str, Any]:
    return await call(ctx, "gwl_get_document", path="/documents/" + seg(slug))


@gwl_tool(
    name="gwl_get_document_by_id",
    title="按 id 读文档",
    endpoint="GET /documents/id/{doc_id}",
    description=(
        "按文档 id 读取全文，不增加浏览数，是编辑前的推荐读法。\n"
        "返回里的 version 就是乐观锁基线，更新时原样带回 data.version。\n"
        "软删除的文档只有作者或超管能看到，其他人 404。"
    ),
)
async def gwl_get_document_by_id(
    ctx: Context,
    doc_id: Annotated[DocId, Field(description="文档 id（路径参数）。")],
) -> dict[str, Any]:
    return await call(ctx, "gwl_get_document_by_id", path="/documents/id/" + seg(doc_id))


@gwl_tool(
    name="gwl_update_document",
    title="更新文档（乐观锁）",
    endpoint="PUT /documents/id/{doc_id}",
    read_only=False,
    description=(
        "更新文档：至少要提供一个可变字段"
        "（title/content/summary/icon/folder_id/permission_bits/visibility/owning），"
        "否则本工具直接报参数错误而不发请求。\n"
        "并发安全：强烈建议先 gwl_get_document_by_id 拿到 version 并原样传入；"
        "版本不符返回 409，且 data 带 current_version / expected_version / editor_username。\n"
        "base_content 表示「你准备改动的那份原文」，必须与 content 同时提供，"
        "服务端据此自动合并他人改动。\n"
        "重复提交同样内容返回 msg=无变更（幂等）。revision_summary 是这次修订的说明。"
    ),
)
async def gwl_update_document(
    ctx: Context,
    doc_id: Annotated[DocId, Field(description="文档 id（路径参数）。")],
    title: Title | None = None,
    content: Content | None = None,
    summary: Summary | None = None,
    icon: Icon | None = None,
    folder_id: Annotated[int | None, Field(description="移动到指定文件夹（0=根目录）。", ge=0)] = None,
    permission_bits: PermissionBits | None = None,
    visibility: Visibility | None = None,
    owning: Owning | None = None,
    revision_summary: Annotated[str | None, Field(description="本次修订说明（写进修订历史），最多 200 个字符。", max_length=200)] = None,
    version: Annotated[int | None, Field(description="乐观锁基线版本；来自 gwl_get_document_by_id 的 version。省略=不做版本校验。", ge=1)] = None,
    base_content: Annotated[str | None, Field(description="准备改动的原文基线；必须与 content 同时提供。")] = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "title": title,
        "content": content,
        "summary": summary,
        "icon": icon,
        "folder_id": folder_id,
        "permission_bits": permission_bits,
        "visibility": visibility,
        "owning": owning,
        "revision_summary": revision_summary,
        "version": version,
        "base_content": base_content,
    }
    if body["base_content"] is not None and body["content"] is None:
        raise ValidationError(
            "base_content 只在同时提供 content 时才有意义（它是我准备改动的原文基线）。",
            detail={"hint": "先 gwl_get_document_by_id 取 content，再带 content + base_content 提交。"},
        )
    if not any(key in _MUTABLE and body[key] is not None for key in body):
        raise ValidationError(
            "gwl_update_document 至少要修改一个字段：" + "、".join(_MUTABLE) + "。",
            detail={"mutable_fields": list(_MUTABLE)},
        )
    return await call(ctx, "gwl_update_document", path="/documents/id/" + seg(doc_id), body=body)


@gwl_tool(
    name="gwl_delete_document",
    title="删除文档（软删除）",
    endpoint="DELETE /documents/id/{doc_id}",
    read_only=False,
    destructive=True,
    description=(
        "把文档移入回收站（软删除，可用 gwl_restore_document 恢复）。\n"
        "仅作者或被授权者，否则 403；已经是回收站状态则 404。\n"
        "不可恢复的永久删除接口本服务刻意不提供。"
    ),
)
async def gwl_delete_document(
    ctx: Context,
    doc_id: Annotated[DocId, Field(description="文档 id（路径参数）。")],
) -> dict[str, Any]:
    return await call(ctx, "gwl_delete_document", path="/documents/id/" + seg(doc_id))


@gwl_tool(
    name="gwl_list_document_revisions",
    title="修订历史",
    endpoint="GET /documents/id/{doc_id}/revisions",
    description=(
        "列出文档的修订历史（裸数组：revision_num、editor_username、summary、has_content、created_at）。\n"
        "不含正文；取某一版全文用 gwl_get_document_revision。"
    ),
)
async def gwl_list_document_revisions(
    ctx: Context,
    doc_id: Annotated[DocId, Field(description="文档 id（路径参数）。")],
) -> dict[str, Any]:
    return await call(ctx, "gwl_list_document_revisions", path="/documents/id/" + seg(doc_id) + "/revisions")


@gwl_tool(
    name="gwl_get_document_revision",
    title="某版修订全文",
    endpoint="GET /documents/id/{doc_id}/revisions/{rev}",
    description=(
        "读取某一版修订的全文，并带 current_version 与 can_restore。\n"
        "该修订不存在 404；没有内容快照 404。"
    ),
)
async def gwl_get_document_revision(
    ctx: Context,
    doc_id: Annotated[DocId, Field(description="文档 id（路径参数）。")],
    rev: Annotated[Rev, Field(description="修订号（路径参数）。")],
) -> dict[str, Any]:
    path = "/documents/id/" + seg(doc_id) + "/revisions/" + seg(rev)
    return await call(ctx, "gwl_get_document_revision", path=path)


@gwl_tool(
    name="gwl_restore_document_revision",
    title="恢复到某版",
    endpoint="POST /documents/id/{doc_id}/revisions/{rev}",
    read_only=False,
    idempotent=False,
    description=(
        "把文档正文恢复到某一版修订（会新写一条修订记录，因此本身可逆）。\n"
        "需要可编辑权限（403），同样受乐观锁保护：建议带 version。\n"
        "当前正文已经与该版一致时返回 400（重复调用不幂等）。"
    ),
)
async def gwl_restore_document_revision(
    ctx: Context,
    doc_id: Annotated[DocId, Field(description="文档 id（路径参数）。")],
    rev: Annotated[Rev, Field(description="要恢复到的修订号（路径参数）。")],
    version: Annotated[int | None, Field(description="乐观锁基线版本；省略=不做版本校验。", ge=1)] = None,
) -> dict[str, Any]:
    path = "/documents/id/" + seg(doc_id) + "/revisions/" + seg(rev)
    return await call(ctx, "gwl_restore_document_revision", path=path, body={"version": version})


@gwl_tool(
    name="gwl_restore_document",
    title="从回收站恢复",
    endpoint="POST /documents/id/{doc_id}/restore",
    read_only=False,
    idempotent=False,
    description=(
        "把软删除的文档从回收站恢复回来。\n"
        "文档不在回收站 400；无权恢复 403。恢复后返回文档详情（msg=恢复成功）。"
    ),
)
async def gwl_restore_document(
    ctx: Context,
    doc_id: Annotated[DocId, Field(description="文档 id（路径参数）。")],
) -> dict[str, Any]:
    return await call(ctx, "gwl_restore_document", path="/documents/id/" + seg(doc_id) + "/restore")