"""日志：只写 stderr，绝不碰 stdout。

stdio 传输下 stdout 是 JSON-RPC 通道。官方 SDK 的 stdio_server() 已经把 fd 1
改指向 stderr（协议帧走私有 dup），因此这里的职责是：

1. 保证我们自己的日志一定落在 stderr；
2. 保证 Windows 控制台（默认 GBK 码页）不会因为中文而抛 UnicodeEncodeError，
   从而把日志吞掉 —— 优先用 UTF-8，失败再退回 replace。

用法：任何入口（server.py / run.py / 探针）第一件事就调用 configure()。
"""
from __future__ import annotations

import logging
import sys
from typing import TextIO

LOGGER_NAME = "gwl_mcp"
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_HANDLER_FLAG = "_gwl_mcp_stderr_handler"


def configure(level: str = "WARNING", *, stream: TextIO | None = None) -> logging.Logger:
    """把 gwl_mcp 这一棵 logger 接到 stderr（幂等）。

    同时接管 root logger：uvicorn / anyio / SDK 内部的告警也必须走 stderr，
    否则 stdio 模式下它们的输出会混进协议通道。
    """
    target = stream if stream is not None else _stderr_stream()
    resolved = getattr(logging, str(level).upper(), logging.WARNING)

    root = logging.getLogger()
    existing = [h for h in root.handlers if getattr(h, _HANDLER_FLAG, False)]
    if not existing:
        handler = logging.StreamHandler(target)
        setattr(handler, _HANDLER_FLAG, True)
        handler.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(handler)
    else:
        for handler in existing:
            handler.setStream(target)
    root.setLevel(resolved)

    logging.getLogger(LOGGER_NAME).setLevel(resolved)
    for noisy in ("uvicorn.access", "uvicorn.error", "httpcore", "anyio"):
        logging.getLogger(noisy).setLevel(max(resolved, logging.WARNING))
    return logging.getLogger(LOGGER_NAME)


def _stderr_stream() -> TextIO:
    """返回一个能写出中文的 stderr。"""
    stream = sys.stderr
    if stream is None:  # 极端情况：被重定向到不存在 stderr 的宿主
        import os

        try:
            return open(os.devnull, "w", encoding="utf-8")
        except OSError:
            class _Null:
                def write(self, data: str) -> int:
                    return len(data)

                def flush(self) -> None:
                    return None

            return _Null()  # type: ignore[return-value]
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError, OSError):
        pass
    return stream


def log_tool_call(logger: logging.Logger, tool: str, endpoint: str, *, ok: bool,
                  status: int | None, attempts: int, elapsed_ms: int, msg: str = "") -> None:
    """统一的工具调用日志行（不含任何凭据、不含参数值，只含端点与结果口径）。"""
    line = "%s %s -> %s status=%s attempts=%s %dms" % (
        tool, endpoint, "ok" if ok else "error", status, attempts, elapsed_ms
    )
    if msg:
        line += " msg=%s" % msg[:120]
    logger.log(logging.INFO if ok else logging.WARNING, line)
