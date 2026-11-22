"""gwl_mcp.tools：按业务域拆分的工具实现。

导入本包即完成全部工具声明的收集；install(server) 负责交给官方 SDK。
"""
from __future__ import annotations

from ..registry import install, names, ordered
from . import account, documents, notifications, org, taxonomy  # noqa: F401  (触发注册)

__all__ = ["install", "names", "ordered"]
