"""把 tests/fake_upstream.py 的假上游作为独立进程跑起来，便于手工联调与 MCP Inspector 端到端验收。

用法（端口固定更好配，默认 5099）：

    python scripts/serve_fake_upstream.py --port 5099

打印出的三行直接抄进临时 .env 即可：

    GWL_API_BASE=http://127.0.0.1:5099/api/v1
    GWL_USER_ID=42
    GWL_API_KEY=gk_TESTONLY000...

假上游默认**回显**请求（method/path/query/body），所以能验证：

* 工具调用链路真的走到了 HTTP 层，且鉴权只走请求头；
* 参数是怎么编码进 query/body 的；
* 错误映射（排入 401/403/404/409/429/500 后 tools/call 的返回）。

只用于本地开发，不含任何真实凭据。
"""
from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fake_upstream import FAKE_API_KEY, FAKE_USER_ID, FakeGwl  # noqa: E402


class FixedPortFakeGwl(FakeGwl):
    """FakeGwl 的固定端口版本（原版抢随机端口，便于并行测试）。"""

    def __init__(self, port: int) -> None:
        super().__init__()
        self._server.server_close()
        from http.server import ThreadingHTTPServer
        handler_cls = self._server.RequestHandlerClass
        self._server = ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="本地假 GWL /api/v1 上游（仅开发用）")
    parser.add_argument("--port", type=int, default=5099)
    args = parser.parse_args()

    fake = FixedPortFakeGwl(args.port)
    fake.__enter__()
    try:
        import signal
        stop = threading.Event()

        def _stop(*_a: object) -> None:
            stop.set()

        for name in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, name, None)
            if sig is not None:
                try:
                    signal.signal(sig, _stop)
                except (ValueError, OSError):
                    pass
        print(f"fake upstream on {fake.base_url}", flush=True)
        print(f"GWL_USER_ID={FAKE_USER_ID}", flush=True)
        print(f"GWL_API_KEY={FAKE_API_KEY}", flush=True)
        stop.wait()
    finally:
        fake.__exit__()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
