"""stdio 传输的真实子进程端到端测试（本地 MCP Client 的实际运行方式）。

断言的重点不只是「能用」，还有 stdio 特有的两条纪律：

1. stdout 只允许出现 JSON-RPC 行 —— 任何日志/警告/异常提示都不许混进去；
2. 换行与编码：帧必须是 UTF-8 的一行一条 JSON（Windows 上客户端对 CRLF 也宽容，
   但我们记录真实行为，避免以后有人改动流配置时静默破坏协议）。
"""
from __future__ import annotations

import io
import json
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


class StdioPeer:
    def __init__(self, env: dict[str, str]):
        self.process = subprocess.Popen(
            [sys.executable, str(ROOT / "run.py"), "--transport", "stdio"],
            cwd=str(ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        self._lines: queue.Queue[bytes] = queue.Queue()
        self._stderr = io.StringIO()
        self._threads = [
            threading.Thread(target=self._pump, args=(self.process.stdout, self._lines), daemon=True),
            threading.Thread(target=self._pump_text, args=(self.process.stderr, self._stderr), daemon=True),
        ]
        for thread in self._threads:
            thread.start()
        self._counter = 0

    @staticmethod
    def _pump(stream, sink):
        for line in iter(stream.readline, b""):
            sink.put(line)
        stream.close()

    @staticmethod
    def _pump_text(stream, sink):
        for line in iter(stream.readline, b""):
            sink.write(line.decode("utf-8", errors="replace"))
        stream.close()

    def send(self, payload: dict) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        self.process.stdin.flush()

    def request(self, method: str, params: dict | None = None, timeout: float = 30.0) -> dict:
        self._counter += 1
        payload = {"jsonrpc": "2.0", "id": self._counter, "method": method}
        if params is not None:
            payload["params"] = params
        self.send(payload)
        line = self._lines.get(timeout=timeout)
        return json.loads(line.decode("utf-8"))

    def notify(self, method: str, params: dict | None = None) -> None:
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self.send(payload)

    def close(self) -> tuple[int, str, list[bytes]]:
        if self.process.stdin is not None:
            self.process.stdin.close()
        try:
            code = self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:  # pragma: no cover
            self.process.kill()
            code = self.process.wait(timeout=10)
        remaining = []
        while True:
            try:
                remaining.append(self._lines.get_nowait())
            except queue.Empty:
                break
        return code, self._stderr.getvalue(), remaining


@pytest.fixture
def peer(fake_gwl):
    import os

    env = dict(os.environ)
    env.update(fake_gwl.env)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["GWL_LOG_LEVEL"] = "INFO"
    fake_gwl.default(200, {"code": 200, "msg": "pong", "data": {"userid": 42, "ok": True}})
    instance = StdioPeer(env)
    try:
        yield instance
    finally:
        if instance.process.poll() is None:
            instance.process.kill()


def _handshake(peer: StdioPeer) -> dict:
    result = peer.request("initialize", {
        "protocolVersion": "2026-07-28",
        "capabilities": {},
        "clientInfo": {"name": "pytest-stdio", "version": "1"},
    })
    peer.notify("notifications/initialized")
    return result


def test_initialize_and_tools_list_over_stdio(peer, fake_gwl):
    result = _handshake(peer)
    assert result["result"]["protocolVersion"]
    assert "tools" in result["result"]["capabilities"]
    assert result["result"]["serverInfo"]["name"] == "gwl-api-mcp"
    listing = peer.request("tools/list", {})
    tools = listing["result"]["tools"]
    assert len(tools) == 36
    assert {tool["name"] for tool in tools} >= {"gwl_ping", "gwl_search", "gwl_update_document"}


def test_tool_call_roundtrip_and_stdout_purity(peer, fake_gwl):
    _handshake(peer)
    call = peer.request("tools/call", {"name": "gwl_ping", "arguments": {}})
    assert call["result"]["isError"] is False
    body = json.loads(call["result"]["content"][0]["text"])
    assert body["ok"] is True
    assert fake_gwl.last()["path"] == "/api/v1/ping"
    # ping 之后再取一次 tools/list，确认 stdout 没有多出杂音行
    peer.request("tools/list", {})
    code, stderr_text, leftover = peer.close()
    assert leftover == [], "stdout 出现了无法归因的多余行"
    assert "[gwl-mcp]" in stderr_text or "gwl_mcp" in stderr_text


def test_frames_are_one_json_object_per_line(peer, fake_gwl):
    """一行一条 JSON；Windows 上官方 SDK 的文本层会写成 CRLF（客户端按行解析即可）。

    这条测试的意义是「行为被记录」：将来有人改了流编码 / newline 配置，
    这里会立刻失败，而不是让客户端莫名其妙断连。
    """
    _handshake(peer)
    peer.send({"jsonrpc": "2.0", "id": 99, "method": "tools/list", "params": {}})
    raw = peer._lines.get(timeout=30)
    assert raw.count(b"\n") == 1, "一帧里出现了多个换行"
    assert raw.rstrip(b"\r\n").endswith(b"}")
    parsed = json.loads(raw.decode("utf-8"))
    assert parsed["id"] == 99
    assert len(parsed["result"]["tools"]) == 36


def test_unknown_tool_and_bad_params_return_is_error(peer, fake_gwl):
    _handshake(peer)
    # 官方 SDK 把「未知工具」也按工具层错误回（isError=true），比 JSON-RPC 错误更好读
    missing = peer.request("tools/call", {"name": "gwl_does_not_exist", "arguments": {}})
    assert missing["result"]["isError"] is True
    assert "gwl_does_not_exist" in missing["result"]["content"][0]["text"]
    bad = peer.request("tools/call", {"name": "gwl_get_document_by_id", "arguments": {"doc_id": "x"}})
    assert bad["result"]["isError"] is True
    assert "validation error" in bad["result"]["content"][0]["text"]


def test_resources_over_stdio(peer, fake_gwl):
    _handshake(peer)
    listing = peer.request("resources/list", {})
    uris = {item["uri"] for item in listing["result"]["resources"]}
    assert "gwl://tools/inventory" in uris and "gwl://api/overview" in uris
    templates = peer.request("resources/templates/list", {})
    assert any("{doc_id}" in item["uriTemplate"] for item in templates["result"]["resourceTemplates"])
    read = peer.request("resources/read", {"uri": "gwl://me"})
    assert "contents" in read["result"]
    unknown = peer.request("resources/read", {"uri": "gwl://nope"})
    assert "error" in unknown


def test_ping_and_unknown_method(peer, fake_gwl):
    _handshake(peer)
    assert peer.request("ping", {})["result"] is not None
    nope = peer.request("tasks/get", {"id": "x"})
    assert nope["error"]["code"] == -32601


def test_credentials_never_appear_on_stdout_or_stderr(peer, fake_gwl):
    _handshake(peer)
    peer.request("tools/call", {"name": "gwl_get_me", "arguments": {}})
    peer.request("tools/list", {})
    code, stderr_text, leftover = peer.close()
    blob = stderr_text + b"".join(leftover).decode("utf-8", errors="replace")
    assert fake_gwl.env["GWL_API_KEY"] not in blob
    assert "X-Api-Key" not in blob


def test_clean_exit_when_client_closes_stdin(peer, fake_gwl):
    _handshake(peer)
    code, _stderr, _leftover = peer.close()
    assert code == 0, "客户端关闭 stdin 后服务应干净退出"