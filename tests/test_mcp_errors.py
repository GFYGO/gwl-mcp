"""透过 MCP 协议层看错误：上游错误、参数校验、只读开关都必须变成可读的 isError。"""
from __future__ import annotations

import json

import pytest

from conftest import mcp_client

def payload_of(result) -> dict:
    """从 isError 的文本里取回结构化尾巴（第一行是中文原因）。"""
    text = result.content[0].text
    if "\n" in text:
        head, tail = text.split("\n", 1)
        try:
            merged = {"message": head}
            merged.update(json.loads(tail))
            return merged
        except json.JSONDecodeError:
            return {"message": text}
    return {"message": text}


async def test_credentials_never_leak_into_tool_results(server, fake):
    fake.queue(401, {"code": 401, "msg": "用户 ID 或 API Key 无效", "data": None})
    async with mcp_client(server) as client:
        result = await client.call_tool("gwl_ping", {})
    assert result.is_error
    body = json.dumps(result.model_dump(), ensure_ascii=False)
    assert fake.env["GWL_API_KEY"] not in body


async def test_http_error_mapping_is_actionable(server, fake):
    cases = [
        (401, {"code": 401, "msg": "缺少 userid 或 apikey", "data": None}, ["GWL_USER_ID", "401"]),
        (403, {"code": 403, "msg": "权限不足", "data": None}, ["权限", "403"]),
        (404, {"code": 404, "msg": "文档不存在", "data": None}, ["doc_id", "404"]),
        (409, {"code": 409, "msg": "版本冲突", "data": {"current_version": 9,
                                                        "expected_version": 4,
                                                        "editor_username": "bob"}},
         ["409", "当前版本=9", "提交版本=4", "重新读取"]),
        (429, {"code": 429, "msg": "请求过于频繁，请 12 秒后重试", "data": None}, ["限流", "120"]),
        (413, {"code": 413, "msg": "请求体过大", "data": None}, ["16MB"]),
        (500, {"code": 500, "msg": "服务器内部错误", "data": None}, ["上游服务暂时不可用"]),
    ]
    async with mcp_client(server) as client:
        for status, body, expectations in cases:
            fake.reset()
            # 429 会先重试：把每次尝试都排成同样的错误
            for _ in range(4):
                fake.queue(status, body, {"Retry-After": "1"} if status == 429 else {})
            result = await client.call_tool("gwl_get_document_by_id", {"doc_id": 1})
            assert result.is_error, status
            text = result.content[0].text
            for token in expectations:
                assert token in text, (status, token, text)


async def test_unknown_parameter_is_rejected_with_allowed_list(server, fake):
    async with mcp_client(server) as client:
        result = await client.call_tool("gwl_list_documents", {"scope": "public", "page_size": 20})
    assert result.is_error
    text = result.content[0].text
    assert "未知参数" in text and "page_size" in text and "folder_id" in text
    assert fake.requests == [], "参数被拒时不该发任何上游请求"


async def test_type_and_enum_errors_come_from_schema(server, fake):
    async with mcp_client(server) as client:
        r1 = await client.call_tool("gwl_get_document_by_id", {"doc_id": "abc"})
        r2 = await client.call_tool("gwl_list_documents", {"scope": "everybody"})
        r3 = await client.call_tool("gwl_list_documents", {"size": 5000})
        r4 = await client.call_tool("gwl_get_class", {"cid": "toolong"})
    assert r1.is_error and r2.is_error and r3.is_error and r4.is_error
    assert "validation error" in r1.content[0].text or "Input should" in r1.content[0].text
    assert "public" in r2.content[0].text  # 枚举错误里报出可选值
    assert fake.requests == []


async def test_missing_required_argument(server, fake):
    async with mcp_client(server) as client:
        result = await client.call_tool("gwl_search", {})
    assert result.is_error
    assert "q" in result.content[0].text


async def test_cross_field_rules_run_before_any_http(server, fake):
    async with mcp_client(server) as client:
        r1 = await client.call_tool("gwl_update_document", {"doc_id": 1})
        r2 = await client.call_tool("gwl_update_document", {"doc_id": 1, "base_content": "x"})
        r3 = await client.call_tool("gwl_mark_notifications_read", {})
        r4 = await client.call_tool("gwl_mark_notifications_read", {"global_ids": [], "personal_ids": []})
    assert r1.is_error and "至少要修改一个字段" in r1.content[0].text
    assert r2.is_error and "base_content" in r2.content[0].text
    assert r3.is_error and "至少一个非空" in r3.content[0].text
    assert r4.is_error
    assert fake.requests == []


async def test_read_only_mode_blocks_writes_but_not_reads(fake):
    from gwl_mcp.config import Config
    from gwl_mcp.server import create_server

    server = create_server(Config.from_env({**fake.env, "GWL_ENABLE_WRITES": "0"}))
    async with mcp_client(server) as client:
        ok = await client.call_tool("gwl_ping", {})
        blocked = await client.call_tool("gwl_create_document", {"title": "T"})
        blocked2 = await client.call_tool("gwl_delete_document", {"doc_id": 3})
    assert not ok.is_error
    assert blocked.is_error and "GWL_ENABLE_WRITES=0" in blocked.content[0].text
    assert blocked2.is_error and "DELETE /documents/id/{doc_id}" in blocked2.content[0].text
    assert [req["method"] for req in fake.requests] == ["GET"], "写工具必须在上游之前被拦下"


async def test_success_result_shape(server, fake):
    fake.succeed_with({"items": [{"id": 1}], "page": 1, "total": 1})
    async with mcp_client(server) as client:
        result = await client.call_tool("gwl_list_documents", {})
    assert not result.is_error
    data = json.loads(result.content[0].text)
    assert data["ok"] is True and data["data"]["items"][0]["id"] == 1
    assert result.structured_content is not None, "官方 SDK 应同时给出 structuredContent"


async def test_error_notifications_go_to_stderr_not_the_protocol(server, fake, caplog):
    """可预期错误：协议里是 isError，日志里是 WARNING（stderr），两者都不含密钥。"""
    fake.queue(403, {"code": 403, "msg": "权限不足", "data": None})
    with caplog.at_level("INFO", logger="gwl_mcp"):
        async with mcp_client(server) as client:
            await client.call_tool("gwl_get_document_by_id", {"doc_id": 1})
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert "gwl_get_document_by_id" in joined
    assert fake.env["GWL_API_KEY"] not in joined