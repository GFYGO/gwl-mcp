# GWL MCP 服务 v1（官方 Python SDK 实现）

把 **GWL（gwl.net.cn）的 `/api/v1` 用户 API** 封装成 MCP 服务：36 个工具 + 7 个资源，
传输支持 **stdio**（本地编程工具）与 **Streamable HTTP / SSE**（远程或多客户端）。

- 上游权威定义：`site-back/routes/api_v1.py`（37 条路由）、`API-WIKI.md` §1.9、`doc/API-V1-用户接口.md`
- 字段级清单：[docs/API-INVENTORY.md](docs/API-INVENTORY.md)（由代码生成，含每个端点的请求/响应/错误码）
- 线上托管端点（不是本目录的代码）：`https://back.gwl.net.cn/mcp`，说明见工作区 `doc/MCP-服务.md`

## 0. 与 v0 的区别

| 维度 | `mcp-server/v0`（手写） | **`mcp-server/v1`（本目录）** |
|---|---|---|
| 协议实现 | 纯标准库手写 JSON-RPC | 官方 `mcp` Python SDK 2.3（协议版本由 SDK 协商，实测 `2026-07-28`） |
| 传输 | stdio + 极简 HTTP | stdio + Streamable HTTP + 旧版 SSE（双端点） |
| 参数校验 | 手写检查 | Pydantic 注解 → `inputSchema` 自动推导，SDK 侧强校验 |
| 结构化输出 | 无 | `outputSchema` + `structuredContent`（客户端免解析文本） |
| 资源 | 无 | 5 个静态 + 2 个模板资源 |
| 测试 | 少量 | pytest 全套（单元 + 假上游集成 + 子进程 stdio + HTTP/SSE 传输） |
| 定位 | 归档参考 | **推荐使用的实现** |

## 1. 安装

```bash
cd mcp-server/v1
python -m venv .venv                      # Windows: .venv\\Scripts\\python.exe
# 激活后或直接使用 venv 内的解释器
pip install -e ".[dev]"                    # 依赖：mcp>=2.3.0,<3（会带上 starlette/uvicorn/httpx）
```

实测环境：Windows 11 + Python 3.14.7 + `mcp==2.3.0`（`mcp-types==2.3.0`）；
要求 `python >= 3.10`。装完后可用 `gwl-mcp` 命令，也可继续用 `python run.py`。

```bash
gwl-mcp --version            # 版本 + SDK 版本
gwl-mcp --list-tools         # 打印 36 个工具与上游端点对照
gwl-mcp --check              # 用当前环境变量真的打一次上游 /ping
gwl-mcp --transport stdio    # 本地 stdio 服务（默认）
gwl-mcp --transport http     # Streamable HTTP，默认 http://127.0.0.1:8765/mcp
gwl-mcp --transport sse      # 旧版 SSE：GET /sse + POST /messages/
```

## 2. 必填的两件事：身份

上游 `/api/v1` 用 **数字 userid + `gk_` 开头的 apikey** 鉴权，两者都只走请求头
（`X-User-Id` / `X-Api-Key`），**绝不出现在 URL、日志或返回值里**。

- userid：GWL 仪表盘「设置 - API Key」页面上显示的数字 id
- apikey：同一页面创建，形如 `gk_` + 43 位

## 3. 环境变量

完整模板见 [.env.example](.env.example)（`scripts/gen_configs.py` 也照它渲染）。

| 变量 | 默认 | 说明 |
|---|---|---|
| `GWL_USER_ID` | — | **必填**。数字用户 id |
| `GWL_API_KEY` | — | **必填**。`gk_...`；长度 16~200（与上游闸门一致） |
| `GWL_API_BASE` | `https://back.gwl.net.cn/api/v1` | 上游基址；本地联调改 `http://127.0.0.1:5000/api/v1` |
| `GWL_ALLOW_HTTP` | `0` | 上游基址是 `http://` 时必须显式设 `1`（防明文泄露 key） |
| `GWL_ORG_CLASS` | 空 | 3 位组类 id，作为 `X-Org-Class` 附带；**不会因此提权** |
| `GWL_TIMEOUT` | `20` | 单次上游请求超时（秒，1~120） |
| `GWL_TOTAL_TIMEOUT` | `60` | 单次工具调用（含全部重试与退避）的总墙钟预算（秒，5~300，且 ≥ `GWL_TIMEOUT`） |
| `GWL_MAX_RETRIES` | `2` | 额外重试次数（0~5），见 §7 重试策略 |
| `GWL_ENABLE_WRITES` | `1` | `0` = 只读模式，12 个写工具在调用时报错（工具仍在列表里，避免客户端行为突变） |
| `GWL_LOG_LEVEL` | `WARNING` | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`；**日志只写 stderr** |
| `GWL_ENV_FILE` | 空 | 指向一个 `.env` 文件（真实进程环境变量优先于文件） |
| `GWL_HTTP_HOST` | `127.0.0.1` | 只影响 http/sse 传输 |
| `GWL_HTTP_PORT` | `8765` | 0~65535；填 0 表示让系统挑端口 |
| `GWL_HTTP_PATH` | `/mcp` | Streamable HTTP 路径（sse 固定 `/sse`） |
| `GWL_HTTP_AUTH_TOKEN` | 空 | 保护 MCP 端点本身的令牌（**不是** GWL apikey），≥16 字符 |
| `GWL_HTTP_ALLOWED_ORIGINS` | 内置回环 + Inspector | 额外允许的浏览器 Origin，逗号分隔（SDK 精确匹配，端口要写全） |
| `GWL_HTTP_SESSION_TTL` | `1800` | 会话空闲回收（秒，60~86400） |
| `GWL_HTTP_MAX_BODY_MB` | `16` | 请求体上限（MB，1~64） |
| `GWL_HTTP_JSON_RESPONSE` | `0` | `1` = POST 一律用 `application/json` 回应，不升级 SSE（反代会缓冲 SSE 时更稳） |
| `GWL_HTTP_ALLOW_INSECURE` | `0` | 明知风险时允许「非回环绑定 + 不设令牌」（不推荐） |

> 绑非回环地址（如 `0.0.0.0`）却没设 `GWL_HTTP_AUTH_TOKEN` → 启动即 `ConfigError` 拒绝，不会裸奔。

## 4. 启动

### 4.1 stdio（本地编程工具）

```bash
GWL_USER_ID=12 GWL_API_KEY=gk_xxx python run.py --transport stdio
# Windows PowerShell:
$env:GWL_USER_ID=12; $env:GWL_API_KEY="gk_xxx"; .venv\\Scripts\\python.exe run.py --transport stdio
```

stdout **只有** JSON-RPC 帧；启动提示、日志、错误全部走 stderr。

### 4.2 Streamable HTTP

```bash
GWL_USER_ID=12 GWL_API_KEY=gk_xxx \\
GWL_HTTP_AUTH_TOKEN="$(openssl rand -hex 16)" \\
python run.py --transport http --host 127.0.0.1 --port 8765
```

端点 `http://127.0.0.1:8765/mcp`。客户端必须同时接受两种媒体类型：
`Accept: application/json, text/event-stream`（只给 `application/json` 会 406，这是 SDK 行为）。
令牌三种给法任选：`Authorization: Bearer <token>`、`Authorization: ApiKey <token>`、`X-MCP-Token: <token>`；
缺失或错误 → 401 + JSON-RPC `-32001`（用常量时间比较，不泄露"前缀对不对"）。

### 4.3 旧版 SSE（只认 SSE 的老客户端）

```bash
python run.py --transport sse --port 8766     # GET /sse 建流，POST /messages/ 发请求
```

### 4.4 让只支持 stdio 的客户端连远程 HTTP 服务

用官方 `mcp-remote` 做桥（模板：[configs/stdio.bridge.mcp-remote.json](configs/stdio.bridge.mcp-remote.json)）。

## 5. 客户端接入配置

`configs/` 下 11 份模板，占位符统一是 `<PYTHON_EXE>`、`<V1_DIR>`、`<GWL_USER_ID>`、
`<GWL_API_KEY>`、`<GWL_HTTP_AUTH_TOKEN>`。用脚本一次渲染成可直接用的配置：

```bash
.venv/Scripts/python.exe scripts/gen_configs.py --user-id 12 --out configs/generated
# 或只打印一份：scripts/gen_configs.py --print cursor.mcp.json
```

| 模板 | 给谁用 | 传输 |
|---|---|---|
| [claude.desktop.json](configs/claude.desktop.json) | Claude Desktop（`claude_desktop_config.json`） | stdio |
| [claude.code.project.json](configs/claude.code.project.json) | Claude Code（项目 `.mcp.json`） | stdio |
| [cursor.mcp.json](configs/cursor.mcp.json) | Cursor（`.cursor/mcp.json`） | stdio |
| [vscode.mcp.json](configs/vscode.mcp.json) | VS Code / GitHub Copilot（`.vscode/mcp.json`） | stdio |
| [cline.mcp.json](configs/cline.mcp.json) | Cline（`mcp_settings.json`） | stdio |
| [continue.json](configs/continue.json) | Continue（`config.json` 的 `mcpServers` 段） | stdio |
| [windsurf.json](configs/windsurf.json) | Windsurf（`model_context_server_config.json`） | stdio |
| [generic.http.json](configs/generic.http.json) | 任何支持 Streamable HTTP + 自定义头的客户端 | http |
| [hosted.http.json](configs/hosted.http.json) | 直接连线上托管 `/mcp`（用 userid+apikey 头，不用本目录代码） | http |
| [stdio.bridge.mcp-remote.json](configs/stdio.bridge.mcp-remote.json) | 只认 stdio 但想连远程的客户端 | stdio→http 桥 |
| [inspector.command.json](configs/inspector.command.json) | MCP Inspector 的 stdio 启动参数 | stdio |

每份文件都带 `_readme` 字段说明它放到哪里、要替换什么（`_readme` 是注释位，各家客户端都会忽略未知字段）。

## 6. 工具清单（36 个）

命名规则 `gwl_<动词>_<对象>`；类型列：**读** = `readOnlyHint`，**写** / **删** = 有副作用（`删` 另带 `destructiveHint`）。
每个工具的 `_meta["gwl/upstream-endpoint"]` 都写明它对应的上游方法+路径，`gwl://tools/inventory` 资源可直接读这张表。

| 工具 | 标题 | 上游端点 | 类型 | 必填参数 |
|---|---|---|---|---|
| gwl_ping | 连通性与凭据自检 | GET /ping | 读 | — |
| gwl_get_me | 我的资料 | GET /me | 读 | — |
| gwl_get_permissions | 实时权限快照 | GET /permissions | 读 | — |
| gwl_list_my_classes | 我的组类清单 | GET /org/classes | 读 | — |
| gwl_list_my_orgs | 我的组织树 | GET /org/orgs | 读 | — |
| gwl_get_class | 组类详情 | GET /org/classes/{cid} | 读 | cid |
| gwl_list_joinable_classes | 可加入的公开组 | GET /org/joinable | 读 | — |
| gwl_get_join_info | 组的加入状态 | GET /org/classes/{cid}/join-info | 读 | cid |
| gwl_join_class | 加入组类 | POST /org/classes/{cid}/join | 写 | cid |
| gwl_list_my_join_requests | 我提交的申请 | GET /org/join-requests/mine | 读 | — |
| gwl_cancel_join_request | 撤回我的申请 | DELETE /org/join-requests/{request_id} | 删 | request_id |
| gwl_list_class_join_requests | 组收到的申请 | GET /org/classes/{cid}/join-requests | 读 | cid |
| gwl_review_join_request | 审批加入申请 | POST /org/join-requests/{request_id}/review | 写 | request_id |
| gwl_list_blacklist | 组类黑名单 | GET /org/classes/{cid}/blacklist | 读 | cid |
| gwl_add_to_blacklist | 拉黑用户 | POST /org/classes/{cid}/blacklist | 写 | cid, username |
| gwl_remove_from_blacklist | 解除拉黑 | DELETE /org/classes/{cid}/blacklist | 写 | cid, username |
| gwl_list_folders | 文件夹列表 | GET /folders | 读 | — |
| gwl_list_documents | 文档列表（分页） | GET /documents | 读 | — |
| gwl_create_document | 创建文档 | POST /documents | 写 | title |
| gwl_list_my_documents | 我的文档 | GET /documents/mine | 读 | — |
| gwl_list_trash | 我的回收站 | GET /documents/trash | 读 | — |
| gwl_get_document | 按 slug 读文档 | GET /documents/{slug} | 读 | slug |
| gwl_get_document_by_id | 按 id 读文档 | GET /documents/id/{doc_id} | 读 | doc_id |
| gwl_update_document | 更新文档（乐观锁） | PUT /documents/id/{doc_id} | 写 | doc_id |
| gwl_delete_document | 删除文档（软删除） | DELETE /documents/id/{doc_id} | 删 | doc_id |
| gwl_list_document_revisions | 修订历史 | GET /documents/id/{doc_id}/revisions | 读 | doc_id |
| gwl_get_document_revision | 某版修订全文 | GET /documents/id/{doc_id}/revisions/{rev} | 读 | doc_id, rev |
| gwl_restore_document_revision | 恢复到某版 | POST /documents/id/{doc_id}/revisions/{rev} | 写 | doc_id, rev |
| gwl_restore_document | 从回收站恢复 | POST /documents/id/{doc_id}/restore | 写 | doc_id |
| gwl_search | 标题搜索 | GET /search | 读 | q |
| gwl_suggest_tags | 标签联想 | GET /tags/suggest | 读 | — |
| gwl_get_tag_facets | 标签聚合 | GET /tags/facets | 读 | object_type |
| gwl_get_object_tags | 读对象标签 | GET /tags/{object_type}/{object_id} | 读 | object_type, object_id |
| gwl_set_object_tags | 全量替换对象标签 | PUT /tags/{object_type}/{object_id} | 写 | object_type, object_id, tags |
| gwl_list_notifications | 我的通知 | GET /notifications | 读 | — |
| gwl_mark_notifications_read | 标记通知已读 | POST /notifications/read | 写 | — |

刻意**没有**封装的工具：`DELETE /documents/id/<id>/permanent`（永久删除，不可逆，
且没有任何开关能打开）——与线上 `site-back/services/mcp/tools.py` 的决策一致。
37 条上游路由 - 1 条永久删除 = 36 个工具。

## 7. 资源（resource）

| URI | 内容 | 是否打上游 |
|---|---|---|
| `gwl://api/overview` | 基址、鉴权方式、限流口径、超时/重试策略、写保护状态（不含任何密钥） | 否 |
| `gwl://tools/inventory` | 工具 ↔ HTTP 端点对照表（含只读/幂等/参数名） | 否 |
| `gwl://me` | 当前 userid 的资料 | 是 |
| `gwl://permissions` | 实时权限快照 | 是 |
| `gwl://notifications` | 未读通知摘要 | 是 |
| `gwl://documents/by-id/{doc_id}` | 按 id 读文档（模板资源） | 是 |
| `gwl://documents/by-slug/{slug}` | 按 slug 读文档（模板资源） | 是 |

资源读取把上游 404 映射成 `ResourceNotFoundError`，其他错误映射成 `ResourceError`，
不会把上游原文（可能含敏感字段）整段抛给客户端。

## 8. 行为约定（照抄上游实现，不是设想）

### 8.1 请求

- 鉴权头固定 `X-User-Id` / `X-Api-Key`（上游也接受 `userid`/`apikey` 与 `Authorization: ApiKey|Bearer <key>`，本实现统一用规范形式）；设置了 `GWL_ORG_CLASS` 时附带 `X-Org-Class`。
- 写请求体一律包成 `{"data": {...}}`（上游 `api_v1` 的入参约定），值为 None 的字段剔除，不发送客户端没填的键。
- query 编码：None 丢弃、布尔转 1/0、列表转逗号分隔（与上游解析方式一致）。
- 路径参数做 URL 编码（slug 里的空格会变 %20）。
- `User-Agent: gwl-mcp/2.0.0`。**禁止跟随重定向**：3xx 一律当错误返回（带 key 的请求被重定向到别的 host 是泄露面）。

### 8.2 响应

上游统一信封 `{code, msg, data}`，且 **code 等于 HTTP 状态码**；本服务在工具返回值里再加一层：

```json
{ "ok": true, "msg": "成功", "data": { }, "rate_limit": { "remaining": 118, "reset_in": 12 } }
```

`structuredContent` 与 `content[0].text` 是同一份结构（客户端优先读 structuredContent）。
`rate_limit` 只在上游回 `X-RateLimit-*` 头时出现。

### 8.3 限流（上游实测口径，进程内 60 秒滑窗）

| 维度 | 上限 | 触发后 |
|---|---|---|
| 读（GET） | 120/min/userid | 429 + Retry-After |
| 写（POST/PUT/DELETE） | 30/min/userid | 429 + Retry-After |
| IP 总量 | 240/min | 429 |
| 鉴权失败 | 20/min | 429（防爆破，此时别再重试） |

### 8.4 超时与重试

- 429：**任何方法**都重试（含 POST——上游限流是滑窗，重放不会造成重复副作用），
  等待时间取 Retry-After 并夹在 1~30 秒；没有该头时用指数退避。
- 5xx / 连接错误 / 超时：**只重放 GET**（幂等安全）。POST/PUT/DELETE 拿到 5xx 直接报错，绝不盲目重放。
- 重试次数上限 `GWL_MAX_RETRIES`，并且始终受 `GWL_TOTAL_TIMEOUT` 总墙钟预算约束（预算用尽即停）。

### 8.5 错误返回

工具内部把错误转成 MCP 的 `isError: true` + 中文可执行提示 + 机读 JSON 尾巴，例如：

```
用户 ID 或 API Key 无效（HTTP 401）。请检查 GWL_USER_ID 与 GWL_API_KEY 是否匹配；
key 轮换或撤销后旧 key 会立即失效（key 只走请求头，不会进 URL 与日志）。
{"api_code": 401, "http_status": 401}
```

覆盖的状态码：400 / 401 / 403 / 404 / 409（附 current_version 等冲突信息）/ 413 / 429 / 5xx / 非 JSON 响应。
参数校验失败（必填缺失、枚举越界、类型不符、长度超限）由 SDK 的 Pydantic 校验直接报错，不会打到上游。
**未声明的参数会被拒绝**（列出允许的参数名），防止 page_size 这类近义拼错静默生效。

### 8.6 日志

`configure()` 只往 **stderr** 挂 handler（root 与 `gwl_mcp`），并把 httpx/uvicorn.access 等噪音降级；
stdio 模式下 stdout 只有协议帧。工具调用会记一行 INFO（工具名、参数键名、结果、耗时），
**不记录参数值**，因此 key 与文档正文都不会进日志。

## 9. 测试

```bash
.venv/Scripts/python.exe -m pytest            # 全套（实测 182 项全绿，约 30 秒）
.venv/Scripts/python.exe -m pytest -k config  # 只跑配置校验
```

| 文件 | 覆盖 |
|---|---|
| tests/test_config.py | 环境变量解析、边界值、非回环必须令牌、.env 读取、脱敏摘要 |
| tests/test_tools_contract.py | 36 个工具的命名/描述/schema/注解/端点元数据；永久删除未暴露 |
| tests/test_tool_requests.py | 每个工具的 method/path/query/body 逐条比对（假上游抓包）+ 凭据只在头里 |
| tests/test_api_client.py | 信封解析、非 JSON、拒绝重定向、429 全方法重试、Retry-After 夹取、5xx 仅 GET、总预算、限流头、网络错误 |
| tests/test_mcp_errors.py | 错误映射不泄露 key、未知参数/枚举/类型拒绝、跨字段校验、只读模式、结果结构、日志内容 |
| tests/test_http_transport.py | Streamable HTTP + SSE：406/401、会话头、DELETE、json_response 模式、Host 校验 |
| tests/test_stdio_transport.py | 子进程 stdio：握手、stdout 纯净（无日志污染）、每行一帧、未知工具、资源、tasks/get 未实现、无凭据泄露、退出码 |
| tests/test_docs_and_configs.py | 11 份客户端模板合法且无真实密钥、.env.example 覆盖全部变量、README/清单与代码同步 |
| tests/fake_upstream.py | 纯标准库假上游（回显 + 可编排状态码），测试与手工联调都用它 |

辅助脚本：

```bash
.venv/Scripts/python.exe scripts/probe_stdio.py   # 起真子进程走完整 stdio 链路，断言 36 工具/资源/校验
.venv/Scripts/python.exe scripts/serve_fake_upstream.py --port 5099   # 本地假上游（不含真实凭据）
.venv/Scripts/python.exe run.py --transport http --port 8765          # 另开一个终端
.venv/Scripts/python.exe scripts/probe_http.py --url http://127.0.0.1:8765/mcp --token <GWL_HTTP_AUTH_TOKEN>
.venv/Scripts/python.exe scripts/write_inventory.py   # 改了工具就重新生成 docs/API-INVENTORY.md
```

## 10. 验收记录（2026-11-22 本机实测，MCP Inspector v2.13.0）

客户端：`npx -y @modelcontextprotocol/inspector --cli`；上游：`scripts/serve_fake_upstream.py`（回显假上游，凭据 42 / gk_TESTONLY… ，**不是真实 key**）。

| # | 检查 | 传输 | 结果 |
|---|---|---|---|
| 1 | initialize 握手 | stdio / http / sse | 通过；协议版本 2026-07-28（stdio 客户端）、2025-11-25（http 探测客户端）；服务 gwl-api-mcp 2.0.0 |
| 2 | tools/list | stdio | 36 个；--strict 退出码 0（无 schema 可移植性错误）；载荷 63,196 字节 |
| 3 | schema 完整度 | stdio | 36/36 有 description、inputSchema.properties、annotations、_meta 端点、outputSchema |
| 4 | tools/call gwl_ping | stdio | ok:true，落到 GET /api/v1/ping；structuredContent 同步给出 |
| 5 | tools/call gwl_list_documents(scope=public,page=2,size=5) | stdio | 上游 query = scope=public, folder_id=0, page=2, size=5 |
| 6 | tools/call gwl_create_document（写） | stdio | POST /api/v1/documents，body = {"data":{"title","slug","content"}} |
| 7 | 未知参数 scope（该工具无此参数） | stdio | isError:true + 列出允许参数 content/folder_id/icon/owning/permission_bits/slug/summary/title/visibility |
| 8 | 枚举越界 scope=nonsense | stdio | isError:true（Pydantic 校验信息），未打到上游 |
| 9 | 凭据无效（指向线上基址 + 占位 key） | stdio | isError:true + 401 中文提示 + {"api_code":401,"http_status":401}；全文无 key |
| 10 | resources/list | stdio | 5 个静态资源（2 个模板资源走 resources/templates/list） |
| 11 | resources/read gwl://tools/inventory | stdio | 36 行对照表，含 endpoint/read_only/destructive/parameters |
| 12 | resources/read gwl://documents/by-id/7 | stdio | 模板 URI 解析为上游 GET /api/v1/documents/id/7 |
| 13 | GWL_ENABLE_WRITES=0 + gwl_delete_document | stdio | isError:true「写操作已被禁用」；tools/list 仍是 36（读 24 / 写 12），行为可预期 |
| 14 | 无令牌 / 错令牌访问 /mcp | http | 均 401（JSON-RPC -32001 + WWW-Authenticate） |
| 15 | tools/list、tools/call gwl_get_me、resources/read | http | 36 工具；调用落到 GET /api/v1/me；资源 JSON 正常 |
| 16 | tools/list、tools/call gwl_get_permissions | sse | 36 工具；调用落到 GET /api/v1/permissions；无令牌 401 |
| 17 | **真实 MCP Client 接入**（DeepSeek Harness 的 MCP 连接器） | stdio | 导入即连接成功，Host 侧注册 **36 个工具**（健康检查 healthy），客户端读到的 gwl_ping schema 与 tools/list 一致 |


### 用真实 MCP Client 复现第 17 行

DSH / Claude Desktop / Cursor 这类客户端只要能吃 stdio 配置即可，例如通用 JSON（路径与凭据换成本机的）：

```json
{
  "mcpServers": {
    "gwl-mcp-v1": {
      "command": "D:/path/to/mcp-server/v1/.venv/Scripts/python.exe",
      "args": ["D:/path/to/mcp-server/v1/run.py", "--transport", "stdio"],
      "cwd": "D:/path/to/mcp-server/v1",
      "env": { "GWL_USER_ID": "12", "GWL_API_KEY": "gk_你的key", "GWL_LOG_LEVEL": "ERROR" }
    }
  }
}
```

连上之后先确认工具发现是 36 个，再调 `gwl_ping`（回报身份 + 限流口径）；
401 是凭据问题，403 是权限问题（提示里会写清该去哪个页面看）。

## 11. 安全

- **本仓库不含任何真实密钥**：模板里是 `<GWL_API_KEY>` 占位符，测试里是 `gk_TESTONLY…` 假 key；
  `.gitignore` 排除 `.env`、`.env.*`（保留 `.env.example`）与 `configs/*.local.json`。
  `tests/test_docs_and_configs.py` 会扫源码树，出现真实形状的 key 直接测试失败。
- key 只走请求头；日志、错误消息、返回值都不含 key；拒绝重定向，避免跨 host 泄露。
- HTTP/SSE 端点必须自带令牌（`GWL_HTTP_AUTH_TOKEN`，≥16 字符，常量时间比较）；
  非回环绑定不带令牌 → 启动失败（除非显式 `GWL_HTTP_ALLOW_INSECURE=1`）。
- 复用 SDK 的 DNS-rebinding 防护：allowed_hosts 含回环名 + `host:*` 通配端口，
  allowed_origins 默认只放回环与 Inspector（6274/6277），可用 `GWL_HTTP_ALLOWED_ORIGINS` 追加。
- 泄露处置：GWL 仪表盘「设置 - API Key」轮换，旧 key 立即失效（上游语义）。

## 12. 假设与待确认项

实现依据是 `site-back/routes/api_v1.py` + `site-back/utils/api_v1.py` 的代码事实；以下需要产品/上游确认：

1. `POST /documents` 非幂等（重复调用会多一篇）。当前只在描述里提醒、idempotentHint=false，
   **没有**做客户端幂等键；上游若支持 Idempotency-Key 可以再补一层。
2. `GET /documents/trash` 的分页口径按代码同样是 page/size，上游文档未明写。
3. 限流是**进程内**滑窗（多进程/多机部署会翻倍），所以本服务把 429 当作可重试信号而不是硬失败。
4. 上游没有匿名健康检查端点，`--check` 借用 `GET /ping`（会消耗一次读额度）。
5. `X-Org-Class` 只是「以某组类身份发请求」，判定仍在上游；本服务不做任何本地提权假设。
6. MCP 的 logging 能力已被规范弃用（SEP-2577），因此不使用 `ctx.log()`，可观测性依赖 stderr 日志。
7. 未封装 api-key 轮换 / 账号注销类接口：**故意**不让 MCP 客户端碰凭据生命周期。

## 13. 目录结构

```
gwl_mcp/
  config.py        环境变量 → Config（校验 + 脱敏摘要）
  api.py           上游 HTTP 客户端：头、信封、编码、超时/重试、错误映射
  errors.py        错误类型 + 每个状态码的中文提示
  runtime.py       工具调用落到 API 调用；参数严格校验；错误文本
  registry.py      gwl_tool 装饰器 + install(server)（注解、meta、ToolError 包装）
  params.py        复用的参数注解（cid/slug/page/size/tags…）
  resources.py     5 静态 + 2 模板资源
  server.py        MCPServer 组装、Bearer 中间件、http/sse app
  cli.py           命令行入口（--transport/--check/--list-tools/--version）
  tools/           account(3) / org(13) / documents(13) / taxonomy(5) / notifications(2)
configs/           11 份客户端配置模板 + generated/（渲染产物，忽略）
docs/API-INVENTORY.md  由代码生成的字段级清单
scripts/           gen_configs / probe_stdio / probe_http / serve_fake_upstream / write_inventory
tests/             pytest 全套 + 假上游
run.py             免安装直接跑：python run.py --transport stdio
```

## 14. 上游接口变了怎么同步

1. 改/加 `site-back/routes/api_v1.py` 的路由；
2. 在 `gwl_mcp/tools/<域>.py` 增删工具（endpoint 写清方法 + 路径模板）；
3. 更新 `tests/test_tools_contract.py` 的 EXPECTED_TOOLS / EXPECTED_ENDPOINTS；
4. `python scripts/write_inventory.py` 重新生成 `docs/API-INVENTORY.md`；
5. `python -m pytest` 全绿，并同步本 README §6 工具表（可直接从 tools/list 导出）。
