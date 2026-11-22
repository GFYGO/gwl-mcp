"""GWL 用户 API 的 MCP 服务（v1，官方 MCP Python SDK 实现）。

把 https://back.gwl.net.cn/api/v1 的 userid + apikey 用户 API 封装成
Model Context Protocol 工具与资源：

* 传输：stdio（默认）/ Streamable HTTP / 旧版 SSE；
* 依赖：官方 SDK mcp 2.x + Python 3.10+ 标准库（上游 HTTP 用 urllib，不额外引依赖）；
* 凭据：只从环境变量 GWL_USER_ID / GWL_API_KEY 读取，绝不硬编码、绝不入库。

版本只在本文件定义一次（pyproject.toml 用动态读取，避免多处漂移）。
"""

__version__ = "2.0.0"

__all__ = ["__version__"]
