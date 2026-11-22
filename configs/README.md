# 客户端配置模板

这些文件是**模板**：里面的 `<PYTHON_EXE>`、`<V1_DIR>`、`<GWL_USER_ID>`、`<GWL_API_KEY>`、
`<GWL_HTTP_AUTH_TOKEN>` 需要替换成真实值后才能用。**推荐用脚本渲染**，不要手改：

```bash
# 渲染全部模板到 configs/generated/（该目录不入库）
python scripts/gen_configs.py --user-id 12 --out configs/generated

# 只打印某一份（可直接复制粘贴）
python scripts/gen_configs.py --print cursor.mcp.json

# 自检：每份模板渲染后是否仍是合法 JSON
python scripts/gen_configs.py --check
```

`--python` 不填时自动用当前解释器；`--v1-dir` 不填时用本目录绝对路径。

## 每份文件放到哪里

| 文件 | 客户端 | 目标路径 |
|---|---|---|
| claude.desktop.json | Claude Desktop | %APPDATA%/Claude/claude_desktop_config.json（把 mcpServers 段合并进去） |
| claude.code.project.json | Claude Code | 项目根 .mcp.json |
| cursor.mcp.json | Cursor | 项目 .cursor/mcp.json 或全局 mcp.json |
| vscode.mcp.json | VS Code / Copilot | 项目 .vscode/mcp.json |
| cline.mcp.json | Cline（VS Code 扩展） | 其 settings 目录下的 mcp_settings.json |
| continue.json | Continue | ~/.continue/config.json 的 mcpServers 段 |
| windsurf.json | Windsurf | ~/.codeium/windsurf/model_context_server_config.json |
| generic.http.json | 任意支持 Streamable HTTP 的客户端 | 直接填 URL + 令牌头 |
| hosted.http.json | 直连线上托管端点 | https://back.gwl.net.cn/mcp + X-User-Id / X-Api-Key |
| stdio.bridge.mcp-remote.json | 只认 stdio 却要连远程的客户端 | 用 npx mcp-remote 做桥 |
| inspector.command.json | MCP Inspector | 其 Edit Configuration 导入 |

每份 JSON 顶层都有 `_readme` 字段说明用法；客户端会忽略未知字段，删掉也无妨。

## 注意

- **不要**把填了真实 key 的文件提交进 git（本目录 `.gitignore` 已排除 `configs/generated/` 与 `configs/*.local.json`）。
- stdio 配置里的 `env` 会真的启一个 Python 子进程；有些客户端（例如 Inspector CLI）**不会继承父进程环境**，
  所以 key 必须写在 `env` 里，或用 `GWL_ENV_FILE` 指向本机文件。
- 换机器只需改 `<PYTHON_EXE>` 与 `<V1_DIR>` 两处；`cwd` 字段（若有）要与 `<V1_DIR>` 一致，
  否则 `run.py` 的相对路径找不到。
