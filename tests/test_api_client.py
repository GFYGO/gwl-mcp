"""HTTP 客户端层：鉴权头、包络解析、重试与退避、超时预算。"""
from __future__ import annotations

import json

import pytest

from fake_upstream import FAKE_API_KEY
from gwl_mcp.api import ApiClient
from gwl_mcp.config import Config
from gwl_mcp.errors import UpstreamError


def make_client(fake, *, retries=2, timeout=5, total=30, sleep=None):
    env = dict(fake.env)
    env["GWL_MAX_RETRIES"] = str(retries)
    env["GWL_TIMEOUT"] = str(timeout)
    env["GWL_TOTAL_TIMEOUT"] = str(total)
    return ApiClient(Config.from_env(env), sleep=sleep or (lambda _s: None))


def test_auth_headers_and_no_key_in_url(fake):
    client = make_client(fake)
    response = client.get("/ping")
    recorded = fake.last()
    assert recorded["user_id"] == "42"
    assert recorded["api_key"] == FAKE_API_KEY
    assert FAKE_API_KEY not in recorded["path"] + recorded["raw_query"]
    assert response.code == 200


def test_query_encoding_rules(fake):
    client = make_client(fake)
    client.get("/documents", query={"scope": "group", "deleted": True, "tags": ["a", "b"],
                                    "skip": None, "zero": False, "n": 0})
    query = fake.last()["query"]
    assert query == {"scope": "group", "deleted": "1", "tags": "a,b", "zero": "0", "n": "0"}


def test_write_body_is_wrapped_in_data(fake):
    client = make_client(fake)
    client.post("/documents", body={"title": "T", "content": None, "folder_id": 0})
    assert fake.last()["body"] == {"data": {"title": "T", "folder_id": 0}}


def test_business_code_not_200_is_error_even_with_http_200(fake):
    """上游 utils/response.py 让 code==HTTP status；若哪天不一致，必须当失败。"""
    client = make_client(fake)
    fake.queue(200, {"code": 400, "msg": "参数不对", "data": None})
    with pytest.raises(UpstreamError) as exc:
        client.get("/search", query={"q": "x"})
    assert exc.value.code == 400
    assert "参数不对" in exc.value.message


def test_non_json_body_explains_gateway(fake):
    client = make_client(fake, retries=0)
    fake.queue(502, None, raw=b"<html>502 Bad Gateway</html>",
               headers={"Content-Type": "text/html"})
    with pytest.raises(UpstreamError) as exc:
        client.get("/ping")
    assert "非 JSON" in exc.value.message
    assert exc.value.non_json is True


def test_redirect_is_refused(fake):
    client = make_client(fake, retries=0)
    fake.queue(302, None, headers={"Location": "http://evil.example/"})
    with pytest.raises(UpstreamError) as exc:
        client.get("/ping")
    assert "重定向" in exc.value.message


def test_429_retries_for_every_method(fake):
    client = make_client(fake)
    slept = []
    client._sleep = slept.append
    fake.queue(429, {"code": 429, "msg": "请求过于频繁", "data": None}, {"Retry-After": "3"})
    fake.queue(200, {"code": 200, "msg": "成功", "data": {"ok": True}})
    response = client.post("/documents", body={"title": "T"})
    assert response.data == {"ok": True}
    assert slept and slept[0] == 3.0  # 按 Retry-After，不自己乱猜
    assert len(fake.requests) == 2


def test_retry_after_is_clamped(fake):
    client = make_client(fake, total=120)
    slept = []
    client._sleep = slept.append
    fake.queue(429, {"code": 429, "msg": "慢一点", "data": None}, {"Retry-After": "900"})
    fake.queue(200, {"code": 200, "msg": "成功", "data": {}})
    client.get("/ping")
    assert slept[0] == 30.0


def test_5xx_retried_for_get_only(fake):
    client = make_client(fake)
    fake.queue(503, {"code": 503, "msg": "网关挂了", "data": None})
    fake.queue(503, {"code": 503, "msg": "网关挂了", "data": None})
    fake.queue(503, {"code": 503, "msg": "网关挂了", "data": None})
    with pytest.raises(UpstreamError):
        client.get("/ping")
    assert len(fake.requests) == 3  # 1 + max_retries(2)

    fake.reset()
    fake.queue(503, {"code": 503, "msg": "网关挂了", "data": None})
    with pytest.raises(UpstreamError):
        client.post("/documents", body={"title": "T"})
    assert len(fake.requests) == 1, "POST 遇到 5xx 绝不重放（可能已建文档）"


def test_total_budget_stops_retrying(fake):
    """退避不能把单次工具调用拖过 GWL_TOTAL_TIMEOUT（客户端超时保护）。"""
    spent = []
    client = make_client(fake, retries=5, timeout=1, total=5)
    client._sleep = lambda seconds: spent.append(seconds)
    for _ in range(6):
        fake.queue(429, {"code": 429, "msg": "太频繁", "data": None}, {"Retry-After": "30"})
    with pytest.raises(UpstreamError):
        client.get("/ping")
    assert sum(spent) <= 5.0
    assert len(fake.requests) < 6, "预算用尽后就不该继续试了"
    assert "Retry-After" or spent  # 至少退避过一次


def test_rate_limit_headers_surface_to_caller(fake):
    client = make_client(fake)
    fake.queue(200, {"code": 200, "msg": "成功", "data": {}},
               {"X-RateLimit-Limit": "120", "X-RateLimit-Remaining": "119", "X-RateLimit-Reset": "60"})
    response = client.get("/ping")
    assert response.rate_limit == {"limit": 120, "remaining": 119, "reset": 60}
    assert response.envelope()["rate_limit"]["remaining"] == 119


def test_network_error_message_is_actionable(fake, monkeypatch):
    client = make_client(fake)
    import urllib.error
    import urllib.request

    def boom(*_a, **_k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(client._opener, "open", boom)
    with pytest.raises(UpstreamError) as exc:
        client.get("/ping")
    assert "无法连接 GWL API" in exc.value.message
    assert "GWL_API_BASE" in exc.value.message