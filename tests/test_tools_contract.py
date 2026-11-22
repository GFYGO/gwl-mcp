"""工具契约：数量、命名、描述、annotations、inputSchema 与上游端点覆盖。

这些断言是「不许悄悄退化」的防线：

* 36 个工具 = 上游 37 条 v1 路由 - 永久删除（刻意不封装）；
* 每个工具都必须带 title / 足够长的描述 / annotations / 上游端点 meta；
* 每个参数都必须有 description（模型只看得到这些文字）；
* 端点集合必须与 EXPECTED_ENDPOINTS 完全一致（多一个少一个都算失败）。
"""
from __future__ import annotations

import pytest

from gwl_mcp.registry import REGISTRY
from gwl_mcp.server import INSTRUCTIONS

EXPECTED_TOOLS = [
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

EXPECTED_ENDPOINTS = {
    "GET /ping", "GET /me", "GET /permissions",
    "GET /org/classes", "GET /org/orgs", "GET /org/classes/{cid}", "GET /org/joinable",
    "GET /org/classes/{cid}/join-info", "POST /org/classes/{cid}/join", "GET /org/join-requests/mine",
    "DELETE /org/join-requests/{request_id}", "GET /org/classes/{cid}/join-requests",
    "POST /org/join-requests/{request_id}/review", "GET /org/classes/{cid}/blacklist",
    "POST /org/classes/{cid}/blacklist", "DELETE /org/classes/{cid}/blacklist",
    "GET /folders", "GET /documents", "POST /documents", "GET /documents/mine",
    "GET /documents/trash", "GET /documents/{slug}", "GET /documents/id/{doc_id}",
    "PUT /documents/id/{doc_id}", "DELETE /documents/id/{doc_id}",
    "GET /documents/id/{doc_id}/revisions", "GET /documents/id/{doc_id}/revisions/{rev}",
    "POST /documents/id/{doc_id}/revisions/{rev}", "POST /documents/id/{doc_id}/restore",
    "GET /search", "GET /tags/suggest", "GET /tags/facets", "GET /tags/{object_type}/{object_id}",
    "PUT /tags/{object_type}/{object_id}", "GET /notifications", "POST /notifications/read",
}

#: 上游确实存在、但刻意不封装成工具的路由（site-back/routes/api_v1.py:439）
NOT_EXPOSED = {"DELETE /documents/id/{doc_id}/permanent"}


async def test_tool_count_names_and_order(server):
    tools = await server.list_tools()
    names = [tool.name for tool in tools]
    assert len(names) == 36
    assert len(set(names)) == 36
    assert names == EXPECTED_TOOLS, "工具清单或顺序变了，客户端会看到不同结果"


def test_registry_matches_endpoints_and_excludes_permanent_delete():
    specs = {spec.name: spec for spec in REGISTRY}
    assert set(specs) == set(EXPECTED_TOOLS)
    endpoints = {spec.endpoint for spec in REGISTRY}
    assert endpoints == EXPECTED_ENDPOINTS
    assert not (endpoints & NOT_EXPOSED), "永久删除永远不能成为工具"


@pytest.mark.parametrize("spec", sorted(REGISTRY, key=lambda s: s.name), ids=lambda s: s.name)
def test_spec_metadata(spec):
    assert spec.name.startswith("gwl_")
    assert spec.title and len(spec.title) >= 4
    assert len(spec.description) >= 40, "描述太短，模型无法判断权限与副作用"
    assert spec.endpoint.split(" ")[0] in ("GET", "POST", "PUT", "DELETE")
    if spec.read_only:
        assert spec.destructive is False


def test_hints_are_coherent():
    by_name = {spec.name: spec for spec in REGISTRY}
    # 唯一「只读但有写副作用」的工具：按 slug 读会 view_count+1
    assert by_name["gwl_get_document"].read_only is True
    assert by_name["gwl_get_document"].idempotent is False
    assert by_name["gwl_get_document_by_id"].idempotent is True
    # 破坏性操作只有两个（都是可恢复或可重提的）
    destructive = sorted(n for n, s in by_name.items() if s.destructive)
    assert destructive == ["gwl_cancel_join_request", "gwl_delete_document"]
    # POST 建新资源不幂等
    assert by_name["gwl_create_document"].idempotent is False
    # PUT 全量替换标签幂等
    assert by_name["gwl_set_object_tags"].idempotent is True


async def test_schema_shape_and_descriptions(server):
    tools = await server.list_tools()
    for tool in tools:
        schema = tool.input_schema
        assert schema.get("type") == "object"
        props = schema.get("properties") or {}
        assert "ctx" not in props, "Context 参数泄漏进了 inputSchema"
        for pname, prop in props.items():
            text = _collect_descriptions(prop)
            assert len(text) >= 4, "%s.%s 缺 description" % (tool.name, pname)
        for req in schema.get("required") or []:
            assert req in props, "%s 的 required 含不存在的参数 %s" % (tool.name, req)
        annotations = tool.annotations
        assert annotations is not None and annotations.read_only_hint is not None


async def test_every_tool_declares_its_endpoint_meta(server):
    tools = await server.list_tools()
    from gwl_mcp.runtime import ENDPOINT_META_KEY

    for tool in tools:
        meta = tool.meta or {}
        assert meta.get(ENDPOINT_META_KEY), "%s 没声明上游端点" % tool.name


def test_instructions_cover_the_dangerous_bits():
    assert "gwl_ping" in INSTRUCTIONS
    assert "view_count" in INSTRUCTIONS
    assert "version" in INSTRUCTIONS
    assert "永久删除" in INSTRUCTIONS


def _collect_descriptions(node) -> str:
    """SDK 对 Optional 参数生成 anyOf，description 可能挂在分支里。"""
    out: list[str] = []
    if isinstance(node, dict):
        if isinstance(node.get("description"), str):
            out.append(node["description"])
        for key, value in node.items():
            if key == "description":
                continue
            out.append(_collect_descriptions(value))
    elif isinstance(node, list):
        for item in node:
            out.append(_collect_descriptions(item))
    return " ".join(part for part in out if part)
