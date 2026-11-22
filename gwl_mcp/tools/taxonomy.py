"""搜索与标签域：5 个工具（/search、/tags/*）。

注意两个上游口径：
* /search 只搜标题，类型只认 document/organization/group/discover（用户文档里的
  discover_item 在工具层映射为 discover）；命中超过扫描上限（500）会带 scan_truncated。
* 标签写入是「全量替换」，不是追加；上限 8 个标签、每个 24 字。
"""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from mcp.server.mcpserver.context import Context

from ..params import (
    FacetLimit,
    ObjectType,
    ObjectId,
    Page,
    Scope,
    SearchType,
    Size,
    TagLimit,
    TagList,
    TagMode,
)
from ..registry import call, gwl_tool, seg, tags_csv, types_csv


@gwl_tool(
    name="gwl_search",
    title="标题搜索",
    endpoint="GET /search",
    description=(
        "按标题跨对象搜索（文档 / 组织 / 组 / 发现条目），逐条判权后分页返回。\n"
        "data 里除分页包络外还有 q、types[]、counts{document,organization,group,discover}，"
        "items 每项含 type、id、title、summary、icon、url、matched 与范围字段。\n"
        "types 可多值；传 discover_item 会被映射成上游的 discover。\n"
        "q 必填且不超过 64 个字符，否则 400。命中量超过扫描上限时返回 scan_truncated=true。"
    ),
)
async def gwl_search(
    ctx: Context,
    q: Annotated[str, Field(description="标题关键词（1~64 个字符）。", min_length=1, max_length=64)],
    types: Annotated[list[SearchType] | None, Field(
        description="限定搜索对象类型；省略=全部。discover_item 会自动映射为上游的 discover。",
        max_length=5)] = None,
    scope: Annotated[Scope | None, Field(description="文档范围过滤（仅对文档类结果生效）。")] = None,
    tags: Annotated[TagList | None, Field(description="标签过滤（最多 8 个）。")] = None,
    tag_mode: Annotated[TagMode | None, Field(description="标签命中方式：all=同时具备，any=任一具备。")] = None,
    page: Page = 1,
    size: Size = 20,
) -> dict[str, Any]:
    return await call(
        ctx, "gwl_search",
        query={
            "q": q,
            "types": types_csv(types),
            "scope": scope,
            "tags": tags_csv(tags),
            "tag_mode": tag_mode,
            "page": page,
            "size": size,
        },
    )


@gwl_tool(
    name="gwl_suggest_tags",
    title="标签联想",
    endpoint="GET /tags/suggest",
    description=(
        "标签输入联想；q 省略或为空时返回热门标签。\n"
        "返回 {q, items[{id,name,count}]}，count 是该标签被使用的次数。"
    ),
)
async def gwl_suggest_tags(
    ctx: Context,
    q: Annotated[str | None, Field(description="联想前缀（最多 64 个字符）。省略=取热门标签。", max_length=64)] = None,
    limit: TagLimit = 10,
) -> dict[str, Any]:
    return await call(ctx, "gwl_suggest_tags", query={"q": q, "limit": limit})


@gwl_tool(
    name="gwl_get_tag_facets",
    title="标签聚合",
    endpoint="GET /tags/facets",
    description=(
        "按对象类型聚合标签用量，返回 {object_type, scope, items[{id,name,count}]}。\n"
        "object_type 必填；scope 只对 document 生效；不支持的类型返回 400。"
    ),
)
async def gwl_get_tag_facets(
    ctx: Context,
    object_type: Annotated[ObjectType, Field(description="要聚合的对象类型。")],
    scope: Annotated[Scope | None, Field(description="文档范围过滤（仅 object_type=document 生效）。")] = None,
    limit: FacetLimit = 50,
) -> dict[str, Any]:
    return await call(ctx, "gwl_get_tag_facets",
                      query={"object_type": object_type, "scope": scope, "limit": limit})


@gwl_tool(
    name="gwl_get_object_tags",
    title="读对象标签",
    endpoint="GET /tags/{object_type}/{object_id}",
    description=(
        "读取某个对象当前的标签（裸数组 [{id,name}]，按名称排序）。\n"
        "能看到对象就能看到标签；看不到对象 404 / 403。\n"
        "object_id 用字符串：文档传 doc_id，组织/组传 3 位 cid，发现条目传条目 id。"
    ),
)
async def gwl_get_object_tags(
    ctx: Context,
    object_type: Annotated[ObjectType, Field(description="对象类型。")],
    object_id: Annotated[ObjectId, Field(description="对象 id（字符串形式）。")],
) -> dict[str, Any]:
    return await call(ctx, "gwl_get_object_tags",
                      path="/tags/" + seg(object_type) + "/" + seg(object_id))


@gwl_tool(
    name="gwl_set_object_tags",
    title="全量替换对象标签",
    endpoint="PUT /tags/{object_type}/{object_id}",
    read_only=False,
    description=(
        "用给定列表整体替换该对象的标签（不是追加）。\n"
        "tags=[] 表示清空；超过 8 个或单个超过 24 字返回 400；无权编辑该对象返回 403。\n"
        "建议先 gwl_get_object_tags 读回现有标签再合并提交，避免误删他人的标签。"
    ),
)
async def gwl_set_object_tags(
    ctx: Context,
    object_type: Annotated[ObjectType, Field(description="对象类型。")],
    object_id: Annotated[ObjectId, Field(description="对象 id（字符串形式）。")],
    tags: Annotated[TagList, Field(description="新的完整标签列表（空数组=清空，最多 8 个）。")],
) -> dict[str, Any]:
    return await call(
        ctx, "gwl_set_object_tags",
        path="/tags/" + seg(object_type) + "/" + seg(object_id),
        body={"tags": tags},
    )
