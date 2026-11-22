"""Streamable HTTP 传输：真起 uvicorn（随机端口），走真实的 MCP over HTTP 链路。

覆盖交付要求里的「可行时支持 Streamable HTTP」与端点安全：
initialize/会话头、tools/list、tools/call、SSE 内容协商、通知 202、
访问令牌（Bearer 与 X-MCP-Token）、无令牌/错令牌、DELETE 结束会话、
非回环绑定必须带令牌的启动闸门（在 config 层已锁，这里验证 app 能装配）。
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest
import uvicorn

from gwl_mcp.config import Config
from gwl_mcp.server import build_http_app, create_server

TOKEN = "test-tokenshould-be-long-enough"


class _Http:
    def __init__(self, base: str, token: str | None = None):
        self.base = base
        self.token = token
        self.session_id: str | None = None

    def post(self, payload, *, accept="application/json, text/event-stream", token="default",
             extra_headers=None, method="POST"):
        headers = {"Content-Type": "application/json", "Accept": accept}
        if token == "default" and self.token:
            headers["Authorization"] = "Bearer " + self.token
        elif token and token != "default":
            headers["Authorization"] = "Bearer " + token
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        for key, value in (extra_headers or {}).items():
            headers[key] = value
        request = urllib.request.Request(
            self.base, data=json.dumps(payload).encode("utf-8"), headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return {
                    "status": response.status,
                    "headers": {k.lower(): v for k, v in response.headers.items()},
                    "body": response.read().decode("utf-8", errors="replace"),
                }
        except urllib.error.HTTPError as exc:
            return {
                "status": exc.code,
                "headers": {k.lower(): v for k, v in (exc.headers or {}).items()},
                "body": exc.read().decode("utf-8", errors="replace"),
            }


@pytest.fixture(scope="module")
def http_env(fake_gwl):
    """一个模块级的 HTTP 服务实例（带访问令牌）。"""
    env = dict(fake_gwl.env)
    env.update({"GWL_HTTP_AUTH_TOKEN": TOKEN, "GWL_HTTP_HOST": "127.0.0.1", "GWL_HTTP_PORT": "0",
                "GWL_HTTP_PATH": "/mcp"})
    config = Config.from_env(env)
    fake_gwl.default(200, {"code": 200, "msg": "pong", "data": {"userid": 42, "ok": True}})
    app = build_http_app(create_server(config), config, transport="http")
    uv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not uv.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert uv.started, "uvicorn 没起来"
    port = uv.servers[0].sockets[0].getsockname()[1]
    yield "http://127.0.0.1:%d/mcp" % port, TOKEN
    uv.should_exit = True
    thread.join(timeout=20)


def parse_body(result: dict) -> dict:
    """Streamable HTTP 可能回 JSON，也可能回 SSE 帧；两种都要能吃。"""
    content_type = result["headers"].get("content-type", "")
    body = result["body"]
    if "text/event-stream" in content_type:
        payload = None
        for line in body.splitlines():
            if line.startswith("data:"):
                payload = line[5:].strip()
        assert payload, "SSE 响应里没有 data: 帧：%r" % body[:200]
        return json.loads(payload)
    return json.loads(body)


def _handshake(client: _Http) -> dict:
    result = client.post({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2026-07-28", "capabilities": {},
                   "clientInfo": {"name": "pytest", "version": "1"}},
    })
    assert result["status"] == 200, result["body"]
    assert result["headers"].get("mcp-session-id")
    client.session_id = result["headers"].get("mcp-session-id")
    assert client.session_id, "initialize 必须下发 Mcp-Session-Id"
    note = client.post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                       method="POST")
    assert note["status"] in (200, 202), note["body"]
    return result


def test_initialize_negotiates_and_declares_tools(http_env, fake_gwl):
    url, token = http_env
    client = _Http(url, token)
    result = _handshake(client)
    body = parse_body(result)
    caps = body["result"]["capabilities"]
    assert "tools" in caps
    assert body["result"]["serverInfo"]["name"] == "gwl-api-mcp"
    assert "instructions" in body["result"]


def test_tools_list_over_http_has_36(http_env, fake_gwl):
    url, token = http_env
    client = _Http(url, token)
    _handshake(client)
    result = client.post({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert result["status"] == 200, result["body"]
    body = parse_body(result)
    tools = body["result"]["tools"]
    assert len(tools) == 36
    assert all(tool["name"].startswith("gwl_") for tool in tools)
    sample = [tool for tool in tools if tool["name"] == "gwl_search"][0]
    assert sample["inputSchema"]["required"] == ["q"]
    assert sample["annotations"]["readOnlyHint"] is True


def test_tools_call_over_http_reaches_upstream(http_env, fake_gwl):
    url, token = http_env
    fake_gwl.default(200, {"code": 200, "msg": "pong", "data": {"userid": 42}})
    client = _Http(url, token)
    _handshake(client)
    before = len(fake_gwl.requests)
    result = client.post({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                          "params": {"name": "gwl_ping", "arguments": {}}})
    assert result["status"] == 200, result["body"]
    body = parse_body(result)
    assert body["result"]["isError"] is False
    assert len(fake_gwl.requests) == before + 1
    assert fake_gwl.last()["path"] == "/api/v1/ping"


def test_sse_content_negotiation(http_env, fake_gwl):
    """同时接受两种媒体类型时，SDK 用 SSE 帧回；协议要求 Accept 含两者。"""
    url, token = http_env
    client = _Http(url, token)
    _handshake(client)
    result = client.post({"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {}})
    assert result["status"] == 200, result["body"]
    assert "text/event-stream" in result["headers"].get("content-type", "")
    assert "event: message" in result["body"]
    assert parse_body(result)["result"]["tools"]


def test_accept_must_include_both_media_types(http_env, fake_gwl):
    """协议 2026-07-28 要求 POST 的 Accept 同时含 json 与 event-stream，否则 406。"""
    url, token = http_env
    client = _Http(url, token)
    _handshake(client)
    result = client.post({"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {}},
                         accept="application/json")
    assert result["status"] == 406
    assert "Not Acceptable" in result["body"]


def test_accept_without_event_stream_for_post_is_rejected(http_env, fake_gwl):
    url, token = http_env
    client = _Http(url, token)
    _handshake(client)
    result = client.post({"jsonrpc": "2.0", "id": 5, "method": "tools/list", "params": {}},
                         accept="text/plain")
    assert result["status"] == 406


def test_notification_returns_202(http_env, fake_gwl):
    url, token = http_env
    client = _Http(url, token)
    _handshake(client)
    result = client.post({"jsonrpc": "2.0", "method": "notifications/cancelled",
                          "params": {"requestId": "99", "reason": "test"}})
    assert result["status"] == 202


def test_missing_token_is_401(http_env, fake_gwl):
    url, token = http_env
    result = _Http(url, token=None).post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                          "params": {"protocolVersion": "2026-07-28",
                                                     "capabilities": {},
                                                     "clientInfo": {"name": "x", "version": "1"}}},
                                         token=None)
    assert result["status"] == 401
    assert result["headers"].get("www-authenticate", "").startswith("Bearer")
    assert json.loads(result["body"])["error"]["code"] == -32001


def test_wrong_token_is_401(http_env, fake_gwl):
    url, token = http_env
    client = _Http(url, token)
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2026-07-28", "capabilities": {},
                       "clientInfo": {"name": "x", "version": "1"}}}
    result = client.post(body, token="totally-wrong-token-value")
    assert result["status"] == 401
    assert client.session_id is None


def test_x_mcp_token_header_also_works(http_env, fake_gwl):
    url, token = http_env
    client = _Http(url, token=None)
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2026-07-28", "capabilities": {},
                       "clientInfo": {"name": "x", "version": "1"}}}
    result = client.post(body, token=None, extra_headers={"X-MCP-Token": token})
    assert result["status"] == 200, result["body"]


def test_session_id_is_required_after_initialize(http_env, fake_gwl):
    url, token = http_env
    client = _Http(url, token)
    client.session_id = "not-a-real-session"
    result = client.post({"jsonrpc": "2.0", "id": 5, "method": "tools/list", "params": {}})
    assert result["status"] >= 400, result["body"]


def test_delete_ends_the_session(http_env, fake_gwl):
    url, token = http_env
    client = _Http(url, token)
    _handshake(client)
    result = client.post("", method="DELETE")
    assert result["status"] in (200, 204), result["body"]
    again = client.post({"jsonrpc": "2.0", "id": 6, "method": "tools/list", "params": {}})
    assert again["status"] >= 400


def test_dangerous_bind_needs_token(fake):
    """0.0.0.0 无令牌：Config 层就拒绝，压根不给装配 app 的机会。"""
    from gwl_mcp.errors import ConfigError

    with pytest.raises(ConfigError, match="GWL_HTTP_AUTH_TOKEN"):
        Config.from_env({**fake.env, "GWL_HTTP_HOST": "0.0.0.0"})


@pytest.fixture(scope="module")
def http_env_json(fake_gwl):
    """GWL_HTTP_JSON_RESPONSE=1：POST 直接用 application/json 回应（不升级 SSE）。"""
    env = dict(fake_gwl.env)
    env.update({"GWL_HTTP_AUTH_TOKEN": TOKEN, "GWL_HTTP_HOST": "127.0.0.1",
                "GWL_HTTP_PORT": "0", "GWL_HTTP_PATH": "/mcp", "GWL_HTTP_JSON_RESPONSE": "1"})
    config = Config.from_env(env)
    fake_gwl.default(200, {"code": 200, "msg": "pong", "data": {"ok": True}})
    app = build_http_app(create_server(config), config, transport="http")
    uv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not uv.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield "http://127.0.0.1:%d/mcp" % uv.servers[0].sockets[0].getsockname()[1], TOKEN
    uv.should_exit = True
    thread.join(timeout=20)


def test_json_response_mode_returns_plain_json(http_env_json, fake_gwl):
    url, token = http_env_json
    client = _Http(url, token)
    _handshake(client)
    result = client.post({"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {}})
    assert result["status"] == 200, result["body"]
    assert result["headers"].get("content-type", "").startswith("application/json")
    assert len(parse_body(result)["result"]["tools"]) == 36
