"""测试用的假 GWL /api/v1 上游（纯标准库 http.server）。

作用：让集成测试**不需要真实凭据、不碰线上服务**，就能断言

* 每个工具最终发出了什么 HTTP 方法 / 路径 / query / body；
* 鉴权头是不是 X-User-Id / X-Api-Key（以及 key 绝不出现在 URL 里）；
* 401 / 403 / 404 / 409 / 429 / 5xx / 非 JSON 这些真实错误路径的映射；
* 重试行为（429 后第二次成功；POST 的 5xx 不重放）。

用法见 tests/conftest.py。
"""
from __future__ import annotations

import json
import threading
import urllib.parse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

#: 测试专用凭据（假的，不是任何真实 key）
FAKE_USER_ID = 42
FAKE_API_KEY = "gk_" + "TESTONLY" + "0" * 32


class _ScriptedResponse:
    def __init__(self, status: int = 200, payload: Any = None, headers: dict[str, str] | None = None,
                 raw: bytes | None = None):
        self.status = status
        self.payload = payload
        self.headers = headers or {}
        self.raw = raw


class FakeGwl:
    """上下文管理器：with FakeGwl() as fake: ..."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self._queue: deque[_ScriptedResponse] = deque()
        self._default: _ScriptedResponse | None = None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _proxy(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw_body = self.rfile.read(length) if length else b""
                parsed = outer._record(self.command, self.path, raw_body, dict(self.headers))
                outer._respond(self, parsed)

            do_GET = _proxy
            do_POST = _proxy
            do_PUT = _proxy
            do_DELETE = _proxy
            do_PATCH = _proxy

            def log_message(self, *args: Any, **kwargs: Any) -> None:  # 静音
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    # ------------------------------------------------------------------
    def __enter__(self) -> "FakeGwl":
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def base_url(self) -> str:
        return "http://127.0.0.1:%d/api/v1" % self.port

    @property
    def env(self) -> dict[str, str]:
        """直接可喂给 Config.from_env 的环境（含 GWL_ALLOW_HTTP=1）。"""
        return {
            "GWL_API_BASE": self.base_url,
            "GWL_USER_ID": str(FAKE_USER_ID),
            "GWL_API_KEY": FAKE_API_KEY,
            "GWL_ALLOW_HTTP": "1",
            "GWL_TIMEOUT": "5",
            "GWL_TOTAL_TIMEOUT": "10",
            "GWL_MAX_RETRIES": "2",
        }

    # ------------------------------------------------------------------
    # 控制
    # ------------------------------------------------------------------
    def queue(self, status: int = 200, payload: Any = None, headers: dict[str, str] | None = None,
              raw: bytes | None = None) -> None:
        """按顺序排入响应（用完回落到默认响应）。"""
        self._queue.append(_ScriptedResponse(status, payload, headers, raw))

    def default(self, status: int = 200, payload: Any = None, headers: dict[str, str] | None = None,
                raw: bytes | None = None) -> None:
        self._default = _ScriptedResponse(status, payload, headers, raw)

    def succeed_with(self, data: Any, msg: str = "成功") -> None:
        self.default(200, {"code": 200, "msg": msg, "data": data})

    def reset(self) -> None:
        self.requests.clear()
        self._queue.clear()
        self._default = None

    def last(self) -> dict[str, Any]:
        if not self.requests:
            raise AssertionError("假上游没有收到任何请求")
        return self.requests[-1]

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _record(self, method: str, target: str, raw_body: bytes, headers: dict[str, str]) -> _ScriptedResponse:
        split = urllib.parse.urlsplit(target)
        lowered = {k.lower(): v for k, v in headers.items()}
        body: Any = None
        if raw_body:
            try:
                body = json.loads(raw_body.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                body = raw_body.decode("utf-8", errors="replace")
        self.requests.append({
            "method": method,
            "path": split.path,
            "raw_query": split.query,
            "query": {k: v[-1] for k, v in urllib.parse.parse_qs(split.query).items()},
            "body": body,
            "headers": lowered,
            "user_id": lowered.get("x-user-id"),
            "api_key": lowered.get("x-api-key"),
        })
        if self._queue:
            return self._queue.popleft()
        if self._default is not None:
            return self._default
        return _ScriptedResponse(200, {"code": 200, "msg": "成功",
                                       "data": {"echo": {"method": method, "path": split.path,
                                                          "query": dict(urllib.parse.parse_qsl(split.query)),
                                                          "body": body}}})

    def _respond(self, handler: BaseHTTPRequestHandler, scripted: _ScriptedResponse) -> None:
        if scripted.raw is not None:
            payload = scripted.raw
        else:
            payload = json.dumps(scripted.payload, ensure_ascii=False).encode("utf-8")
        handler.send_response(scripted.status)
        content_type = scripted.headers.get("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(payload)))
        for key, value in scripted.headers.items():
            if key.lower() != "content-type":
                handler.send_header(key, value)
        handler.end_headers()
        handler.wfile.write(payload)
