#!/usr/bin/env python3
"""免安装的启动入口（本地 MCP Client 直接 python run.py 即可）。

默认 stdio：

    python run.py
    python run.py --transport http --port 8765
    python run.py --check            # 只验证 GWL 凭据
    python run.py --list-tools       # 打印工具定义（JSON）

依赖官方 MCP SDK（mcp>=2.3,<3）。推荐用本目录的虚拟环境：

    .venv\\Scripts\\python.exe run.py            # Windows
    .venv/bin/python run.py                      # Linux/macOS

或者 pip install -e . 之后直接运行 gwl-mcp。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gwl_mcp.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
