"""每个工具最终发出的 HTTP 请求必须与上游路由逐字一致。

这是本服务最值钱的回归网：工具签名可以演进，但
「方法 + 路径 + query + data 包络」不能变 —— 变了就是打错接口。

同时锁死三条安全不变量：
* 凭据永远在请求头（X-User-Id / X-Api-Key），永远不出现在 URL；
* 写请求的业务参数永远包在 {"data": {...}} 里；
* None 值不下发（上游把 null 与没传同等对待，但省掉更干净）。
"""
from __future__ import annotations

import json

import pytest

from conftest import mcp_client

#: (工具名, 参数, 期望方法, 期望路径, 期望 query, 期望 data)
CASES = [
    ("gwl_ping", {}, "GET", "/api/v1/ping", {}, None),
    ("gwl_get_me", {}, "GET", "/api/v1/me", {}, None),
    ("gwl_get_permissions", {}, "GET", "/api/v1/permissions", {}, None),
    ("gwl_list_my_classes", {}, "GET", "/api/v1/org/classes", {}, None),
    ("gwl_list_my_orgs", {}, "GET", "/api/v1/org/orgs", {}, None),
    ("gwl_get_class", {"cid": "DEV"}, "GET", "/api/v1/org/classes/dev", {}, None),
    ("gwl_list_joinable_classes", {}, "GET", "/api/v1/org/joinable", {}, None),
    ("gwl_list_joinable_classes", {"class_id": "dev"}, "GET", "/api/v1/org/joinable",
     {"class_id": "dev"}, None),
    ("gwl_get_join_info", {"cid": "abc"}, "GET", "/api/v1/org/classes/abc/join-info", {}, None),
    ("gwl_join_class", {"cid": "abc"}, "POST", "/api/v1/org/classes/abc/join", None,
     {"role": "member", "message": ""}),
    ("gwl_join_class", {"cid": "abc", "role": "editor", "message": "请通过"}, "POST",
     "/api/v1/org/classes/abc/join", None, {"role": "editor", "message": "请通过"}),
    ("gwl_list_my_join_requests", {}, "GET", "/api/v1/org/join-requests/mine",
     {"handled": "0"}, None),
    ("gwl_list_my_join_requests", {"handled": True}, "GET", "/api/v1/org/join-requests/mine",
     {"handled": "1"}, None),
    ("gwl_cancel_join_request", {"request_id": 7}, "DELETE", "/api/v1/org/join-requests/7",
     None, None),
    ("gwl_list_class_join_requests", {"cid": "abc"}, "GET", "/api/v1/org/classes/abc/join-requests",
     {"status": "pending"}, None),
    ("gwl_list_class_join_requests", {"cid": "abc", "status": "all"}, "GET",
     "/api/v1/org/classes/abc/join-requests", {"status": "all"}, None),
    ("gwl_review_join_request", {"request_id": 9, "approve": False, "note": "资料不全"}, "POST",
     "/api/v1/org/join-requests/9/review", None, {"approve": False, "note": "资料不全"}),
    ("gwl_list_blacklist", {"cid": "abc"}, "GET", "/api/v1/org/classes/abc/blacklist", {}, None),
    ("gwl_add_to_blacklist", {"cid": "abc", "username": "bob", "reason": "刷屏"}, "POST",
     "/api/v1/org/classes/abc/blacklist", None, {"username": "bob", "reason": "刷屏"}),
    ("gwl_remove_from_blacklist", {"cid": "abc", "username": "bob"}, "DELETE",
     "/api/v1/org/classes/abc/blacklist", None, {"username": "bob"}),
    ("gwl_list_folders", {}, "GET", "/api/v1/folders", {}, None),
    ("gwl_list_folders", {"scope": "group", "parent_id": 0}, "GET", "/api/v1/folders",
     {"scope": "group", "parent_id": "0"}, None),
    ("gwl_list_documents", {}, "GET", "/api/v1/documents",
     {"scope": "public", "folder_id": "0", "page": "1", "size": "20"}, None),
    ("gwl_list_documents", {"scope": "group", "folder_id": -1, "tags": ["算法", "图论"],
                            "tag_mode": "all", "deleted": 1, "author_id": 5, "page": 3, "size": 50},
     "GET", "/api/v1/documents",
     {"scope": "group", "folder_id": "-1", "deleted": "1", "page": "3", "size": "50",
      "tags": "算法,图论", "tag_mode": "all", "author_id": "5"}, None),
    ("gwl_create_document", {"title": "新文档", "content": "正文"}, "POST", "/api/v1/documents",
     None, {"title": "新文档", "content": "正文"}),
    ("gwl_create_document", {"title": "T", "visibility": "group", "owning": "abc",
                             "permission_bits": "110000", "folder_id": 3, "summary": "s", "icon": "📄"},
     "POST", "/api/v1/documents", None,
     {"title": "T", "content": "", "folder_id": 3, "permission_bits": "110000",
      "visibility": "group", "owning": "abc", "summary": "s", "icon": "📄"}),
    ("gwl_list_my_documents", {"folder_id": -1, "page": 2, "size": 5}, "GET",
     "/api/v1/documents/mine", {"folder_id": "-1", "page": "2", "size": "5"}, None),
    ("gwl_list_trash", {}, "GET", "/api/v1/documents/trash", {"page": "1", "size": "20"}, None),
    ("gwl_get_document", {"slug": "my-doc"}, "GET", "/api/v1/documents/my-doc", {}, None),
    # 路径段用 percent-encoding（空格是 %20，不是 +），并挡住 slug 里的斜杠/点号注入
    ("gwl_get_document", {"slug": "中文 slug"}, "GET",
     "/api/v1/documents/%E4%B8%AD%E6%96%87%20slug", {}, None),
    ("gwl_get_document", {"slug": "../admin"}, "GET", "/api/v1/documents/..%2Fadmin", {}, None),
    ("gwl_get_document_by_id", {"doc_id": 12}, "GET", "/api/v1/documents/id/12", {}, None),
    ("gwl_update_document", {"doc_id": 12, "content": "新正文", "version": 4}, "PUT",
     "/api/v1/documents/id/12", None, {"content": "新正文", "version": 4}),
    ("gwl_update_document", {"doc_id": 12, "title": "改标题", "base_content": "旧正文",
                             "content": "新正文"}, "PUT", "/api/v1/documents/id/12", None,
     {"title": "改标题", "content": "新正文", "base_content": "旧正文"}),
    ("gwl_delete_document", {"doc_id": 12}, "DELETE", "/api/v1/documents/id/12", None, None),
    ("gwl_list_document_revisions", {"doc_id": 12}, "GET", "/api/v1/documents/id/12/revisions",
     {}, None),
    ("gwl_get_document_revision", {"doc_id": 12, "rev": 3}, "GET",
     "/api/v1/documents/id/12/revisions/3", {}, None),
    ("gwl_restore_document_revision", {"doc_id": 12, "rev": 3, "version": 8}, "POST",
     "/api/v1/documents/id/12/revisions/3", None, {"version": 8}),
    ("gwl_restore_document", {"doc_id": 12}, "POST", "/api/v1/documents/id/12/restore", None, None),
    ("gwl_search", {"q": "算法"}, "GET", "/api/v1/search", {"q": "算法", "page": "1", "size": "20"},
     None),
    ("gwl_search", {"q": "算法", "types": ["document", "discover_item"], "scope": "public",
                    "tags": ["x"], "tag_mode": "any"}, "GET", "/api/v1/search",
     {"q": "算法", "types": "document,discover", "scope": "public", "tags": "x",
      "tag_mode": "any", "page": "1", "size": "20"}, None),
    ("gwl_suggest_tags", {}, "GET", "/api/v1/tags/suggest", {"limit": "10"}, None),
    ("gwl_suggest_tags", {"q": "算", "limit": 3}, "GET", "/api/v1/tags/suggest",
     {"q": "算", "limit": "3"}, None),
    ("gwl_get_tag_facets", {"object_type": "document"}, "GET", "/api/v1/tags/facets",
     {"object_type": "document", "limit": "50"}, None),
    ("gwl_get_object_tags", {"object_type": "document", "object_id": "12"}, "GET",
     "/api/v1/tags/document/12", {}, None),
    ("gwl_set_object_tags", {"object_type": "group", "object_id": "abc", "tags": ["a", "b"]},
     "PUT", "/api/v1/tags/group/abc", None, {"tags": ["a", "b"]}),
    ("gwl_set_object_tags", {"object_type": "document", "object_id": "12", "tags": []}, "PUT",
     "/api/v1/tags/document/12", None, {"tags": []}),
    ("gwl_list_notifications", {}, "GET", "/api/v1/notifications", {"limit": "100"}, None),
    ("gwl_list_notifications", {"limit": 20}, "GET", "/api/v1/notifications", {"limit": "20"}, None),
    ("gwl_mark_notifications_read", {"global_ids": [1, 2]}, "POST", "/api/v1/notifications/read",
     None, {"global_ids": [1, 2]}),
    ("gwl_mark_notifications_read", {"personal_ids": [3]}, "POST", "/api/v1/notifications/read",
     None, {"personal_ids": [3]}),
]

ALL_TOOLS = sorted({name for name, *_rest in CASES})


@pytest.mark.parametrize("tool,arguments,method,path,query,data", CASES,
                         ids=[case[0] + "#" + str(i) for i, case in enumerate(CASES)])
async def test_tool_emits_expected_request(server, fake_gwl, tool, arguments, method, path, query, data):
    fake_gwl.reset()
    async with mcp_client(server) as client:
        result = await client.call_tool(tool, arguments)
    assert not result.is_error, _text(result)
    recorded = fake_gwl.last()
    assert recorded["method"] == method
    assert recorded["path"] == path
    if query is not None:
        for key, value in query.items():
            assert recorded["query"].get(key) == value, "%s query[%s]" % (tool, key)
        assert set(recorded["query"]) == set(query), "%s 多余/缺少的 query 参数" % tool
    if data is not None:
        assert recorded["body"] == {"data": data}
    else:
        assert recorded["body"] is None or method == "GET"


async def test_credentials_are_headers_only(server, fake_gwl):
    async with mcp_client(server) as client:
        await client.call_tool("gwl_ping", {})
    recorded = fake_gwl.last()
    assert recorded["user_id"] == "42"
    assert recorded["api_key"] == fake_gwl.env["GWL_API_KEY"]
    assert fake_gwl.env["GWL_API_KEY"] not in recorded["path"]
    assert fake_gwl.env["GWL_API_KEY"] not in recorded["raw_query"]


async def test_org_class_header_when_configured(client_server, fake_gwl):
    server = client_server(GWL_ORG_CLASS="dev")
    async with mcp_client(server) as client:
        await client.call_tool("gwl_ping", {})
    assert fake_gwl.last()["headers"].get("x-org-class") == "dev"


async def test_all_tools_are_covered_by_the_table(server):
    tools = await server.list_tools()
    assert sorted(ALL_TOOLS) == sorted(tool.name for tool in tools), "新工具没进请求映射表"
    assert len(tools) == len(ALL_TOOLS) == 36


def _text(result) -> str:
    return "\n".join(part.text for part in result.content if getattr(part, "text", None))