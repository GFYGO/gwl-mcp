#!/usr/bin/env python3
"""生成 docs/API-INVENTORY.md —— 工具/参数/端点清单的唯一出口。

为什么用脚本生成：这份清单必须永远等于实现。手写清单一旦与代码漂移，
按它联调的人一定踩空（根目录 report/ 里已有过一次教训）。

    .venv/Scripts/python.exe scripts/write_inventory.py           # 写文件
    .venv/Scripts/python.exe scripts/write_inventory.py --stdout  # 只打印

上半部分（协议事实）每条结论都指向 site-back 的代码位置；
下半部分（工具表 / 参数表）由 registry + 官方 SDK 生成的 inputSchema 直接导出。
正文里的代码标记用占位符 BQ 表示，最后统一替换成反引号（避免脚本里到处转义）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V1))

from gwl_mcp.config import Config  # noqa: E402
from gwl_mcp.registry import REGISTRY, ordered  # noqa: E402
from gwl_mcp.server import create_server  # noqa: E402

BQ = chr(96)

#: 上游 37 条 v1 用户 API 路由（逐条对着 site-back/routes/api_v1.py 的装饰器数过）
ROUTES = [
    ("GET", "/ping", "认证自检 + 本进程限流口径", "112"),
    ("GET", "/me", "本人资料", "127"),
    ("GET", "/permissions", "实时权限快照", "138"),
    ("GET", "/org/classes", "我的组类平铺清单", "159"),
    ("GET", "/org/orgs", "我的组织（含可见组）", "170"),
    ("GET", "/org/classes/<cid>", "组类详情（仅成员）", "181"),
    ("GET", "/org/joinable", "可自助加入的 public 组", "202"),
    ("GET", "/org/classes/<cid>/join-info", "可见性 + 我的加入状态", "211"),
    ("POST", "/org/classes/<cid>/join", "加入 public / 申请 private", "219"),
    ("GET", "/org/join-requests/mine", "我提交的申请", "230"),
    ("DELETE", "/org/join-requests/<int:request_id>", "撤回我的申请", "240"),
    ("GET", "/org/classes/<cid>/join-requests", "组收到的申请（需 manage）", "248"),
    ("POST", "/org/join-requests/<int:request_id>/review", "批准 / 拒绝申请", "257"),
    ("GET", "/org/classes/<cid>/blacklist", "黑名单（需 manage）", "268"),
    ("POST", "/org/classes/<cid>/blacklist", "拉黑用户", "276"),
    ("DELETE", "/org/classes/<cid>/blacklist", "解除拉黑", "286"),
    ("GET", "/folders", "文件夹列表（不含文档）", "300"),
    ("GET", "/documents", "文档列表（分页，逐条判权）", "313"),
    ("POST", "/documents", "创建文档", "333"),
    ("GET", "/documents/mine", "我的未删文档", "342"),
    ("GET", "/documents/trash", "我的回收站", "352"),
    ("GET", "/documents/<slug>", "按 slug 读全文（view_count+1）", "361"),
    ("GET", "/documents/id/<int:doc_id>", "按 id 读全文（不计数）", "370"),
    ("PUT", "/documents/id/<int:doc_id>", "更新（乐观锁 version / If-Match）", "379"),
    ("DELETE", "/documents/id/<int:doc_id>", "软删除进回收站", "389"),
    ("GET", "/documents/id/<int:doc_id>/revisions", "修订历史（不含正文）", "398"),
    ("GET", "/documents/id/<int:doc_id>/revisions/<int:rev>", "某版修订全文", "407"),
    ("POST", "/documents/id/<int:doc_id>/revisions/<int:rev>", "恢复到某版", "416"),
    ("POST", "/documents/id/<int:doc_id>/restore", "从回收站恢复", "427"),
    ("DELETE", "/documents/id/<int:doc_id>/permanent", "物理删除（不可恢复）", "439"),
    ("GET", "/search", "标题搜索（跨 4 类对象）", "452"),
    ("GET", "/tags/suggest", "标签联想 / 热门", "469"),
    ("GET", "/tags/facets", "标签聚合", "480"),
    ("GET", "/tags/<object_type>/<object_id>", "某对象的标签", "491"),
    ("PUT", "/tags/<object_type>/<object_id>", "全量替换对象标签", "503"),
    ("GET", "/notifications", "个人 + 全局通知 + 未读数", "520"),
    ("POST", "/notifications/read", "标记已读", "542"),
]

HEADER = (
    "# GWL /api/v1 用户 API ↔ MCP 工具 字段级清单（v1）\n\n"
    "> 本文件的工具表与参数表由 BQpython scripts/write_inventory.pyBQ 从代码导出，请勿手改。\n"
    "> 事实来源：BQsite-back/routes/api_v1.pyBQ、BQsite-back/utils/api_v1.pyBQ、"
    "BQsite-back/utils/response.pyBQ、BQsite-back/services/**BQ。"
    "**代码是唯一权威**；与 BQdoc/API-V1-用户接口.mdBQ 冲突时以代码为准。\n\n"
    "## 1. 基址、鉴权与包络\n\n"
    "| 项 | 值 | 证据（site-back 代码位置） |\n|---|---|---|\n"
    "| 线上基址 | https://back.gwl.net.cn/api/v1 | 蓝图 url_prefix=/api/v1（app.py:243）；Nginx 反代到本机 :5000 |\n"
    "| 本地基址 | http://127.0.0.1:5000/api/v1 | app.py:327 |\n"
    "| 鉴权 | userid + apikey，推荐请求头 BQX-User-IdBQ / BQX-Api-KeyBQ | utils/api_v1.py:193-217：头名归一化（大小写/连字符/下划线都认），另接受 BQAuthorization: ApiKey <key>BQ 或 BQBearer <key>BQ（该形式只取 key，不取 userid） |\n"
    "| 凭据优先级 | 请求头 → query → JSON body 顶层 → form | utils/api_v1.py:221-246 |\n"
    "| key 形态 | BQgk_BQ + 43 字符（secrets.token_urlsafe(32)）；入库 sha256$前8位$hex | utils/api_v1.py:48-51,104-111 |\n"
    "| key 长度闸门 | 16~200 | utils/api_v1.py:55-56,443-444 |\n"
    "| 业务参数 | GET 走 query；写请求放 JSON body 的 BQdataBQ 对象（或 body 顶层摊平） | utils/api_v1.py:249-297 |\n"
    "| 组类身份 | BQX-Org-ClassBQ 或 BQ?class=BQ / BQ?_class=BQ；不属于则静默忽略，不报错也不提权 | utils/api_v1.py:300-324 |\n"
    "| 权限口径 | 每次请求实时 resolve_now_permission；BQnow_permission.contextBQ 恒 null（apikey 路径不启用 dashboard 降级） | utils/api_v1.py:305-324 |\n"
    "| 响应包络 | BQ{code, msg, data}BQ；**code 恒等于 HTTP 状态码**，成功 code=200 | utils/response.py:23-34 |\n"
    "| 请求体上限 | 16MB（超出 413） | config.py:187 |\n"
    "| 强制 HTTPS | 应用层不强制（无 ProxyFix / HSTS / 301），TLS 在 Nginx 层 | app.py:176-214 |\n\n"
    "## 2. 限流与超时（本服务的重试据此设计）\n\n"
    "实现是**进程内 60 秒滑动窗口**（utils/decorators.py:24-53），多 worker 各算一份、重启清零，"
    "所以下表是单进程口径。可用 BQAPI_V1_RATE_LIMIT_READ/_WRITE/_IP/_AUTH_FAILBQ 覆盖（config.py:58-61）。\n\n"
    "| 维度 | 桶 | 默认 | 超限返回 |\n|---|---|---|---|\n"
    "| IP（认证前先扣，防拖库） | apiv1:ip:<ip> | 240/分 | 429 + Retry-After + X-RateLimit-* |\n"
    "| 用户读 | apiv1:read:<uid> | 120/分 | 429 |\n"
    "| 用户写（POST/PUT/PATCH/DELETE） | apiv1:write:<uid> | 30/分 | 429 |\n"
    "| 认证失败 | apiv1:authfail:ip 与 :uid 各一份 | 20/分 | 命中即 429 |\n\n"
    "本服务重试策略（BQgwl_mcp/api.pyBQ）：\n\n"
    "| 情况 | 是否重试 | 理由 |\n|---|---|---|\n"
    "| 429 | 任何方法都重试，按 Retry-After（夹在 1~30 秒） | 限流发生在业务执行之前，重放安全 |\n"
    "| 500/502/503/504 | 只重试 GET | 非 GET 可能已提交：重放会重复建文档，或把已成功的撤回变成 409 假错误 |\n"
    "| 连接错误/超时 | 只重试 GET，退避 1/2/4…（上限 8 秒 + 抖动） | 同上 |\n"
    "| 401/403/404/409/413 | 从不重试 | 必须改输入或等人工处理 |\n"
    "| 总预算 | 单次工具调用不超过 BQGWL_TOTAL_TIMEOUTBQ（默认 60 秒） | 不把 MCP 客户端挂死 |\n\n"
    "## 3. 错误码表（v1 可达）\n\n"
    "| code | 触发 | 代表文案（上游原文） |\n|---|---|---|\n"
    "| 200 | 成功 | 成功 / pong / 更新成功 / 无变更 / 已移入回收站 / 恢复成功 / 标签已更新 / 已标记已读 |\n"
    "| 400 | 参数或业务校验 | 组类标识必须是 3 位字母；title 不能为空；该 slug 已被使用；scope 取值无效…；parent_id 必须为整数；该文档不在回收站中；当前正文与该修订一致，无需恢复；标签最多 8 个、每个不超过 24 字；不支持的对象类型；搜索关键词不能为空；role/message/reason 最多 200 个字符 |\n"
    "| 401 | 缺凭据 / userid 非法 / key 长度非法 / 该账号未配 key / key 不匹配 | 缺少 userid 或 apikey（…）；用户 ID 或 API Key 无效；该账号尚未配置 API Key，请先在仪表盘设置中轮换生成 |\n"
    "| 403 | 权限不足，或账户被锁定 | 权限不足；账户已被锁定，请 N 分钟后重试；非管理者；在黑名单；组已停用 |\n"
    "| 404 | 对象/修订/组类/申请不存在；软删文档对非作者也回 404 | 接口不存在（全局 handler）；组类不存在 |\n"
    "| 409 | 乐观锁冲突（**唯一带 data 的错误体**）；申请已处理；已在黑名单 | data 内含 current_version / expected_version / editor_username / doc_id |\n"
    "| 413 | 请求体超过 16MB | werkzeug 默认描述 |\n"
    "| 429 | 任一限流维度超限 | 请求过于频繁，请 N 秒后重试 |\n"
    "| 428 | 需要版本号（当前不可达：REQUIRE_VERSION_ON_UPDATE=False） | 缺少文档版本号…（编辑锁已启用） |\n"
    "| 500 | 未捕获异常（不回显堆栈） | 服务器内部错误；组织操作失败，请稍后重试；数据库操作失败 |\n\n"
    "## 4. 端点总表（37 条）与工具覆盖\n\n"
    "| # | 方法 | 路径 | 用途 | MCP 工具 | api_v1.py 行 |\n|---|---|---|---|---|---|\n"
)

FOOTER = (
    "\n> **36 个工具 = 37 条路由 − 1**：刻意不封装 BQDELETE /documents/id/<int:doc_id>/permanentBQ"
    "（物理删行 + 删盘 + 清 tag_links，不可恢复）。要彻底删除请走站点后台，"
    "或自行用 apikey 直接调该路由；**没有任何环境变量能打开这条工具**。\n"
    "\n## 5. 工具与参数（由代码导出）\n\n### 5.1 速览\n\n"
    "| 工具 | 上游端点 | 只读 | 破坏性 | 幂等 |\n|---|---|---|---|---|\n"
)

TABLE_END = (
    "\n### 5.2 逐个工具的完整参数表\n\n"
    "说明：类型/约束列直接来自官方 SDK 依据函数签名生成的 JSON Schema（BQtools/listBQ 下发的就是它）；"
    "未知参数会被拒绝而不是静默忽略；BQNoneBQ 不会下发给上游；"
    "所有工具都不接受 userid/apikey 参数（凭据只来自环境变量）。\n\n"
)

DIFF_SECTION = (
    "\n## 6. 与 v0 的口径差异（有意为之，不是回归）\n\n"
    "| 项 | v0 | v1 | 原因 |\n|---|---|---|---|\n"
    "| 协议实现 | 手写 JSON-RPC（零依赖） | 官方 SDK BQmcpBQ 2.x | 会话/SSE/CORS/Host 校验/协议协商交给 SDK |\n"
    "| 参数校验 | 自研 JSON Schema 子集校验器 | SDK（pydantic）按生成的 schema 校验；本服务再补未知参数、跨字段约束、只读开关 | 消除 v0 顶层 nullable 形同虚设的漏洞 |\n"
    "| 可选参数类型 | BQtype: [\"integer\",\"null\"]BQ 并集 | BQanyOfBQ | 并集写法在部分客户端/模型上被拒 |\n"
    "| resources | 无（BQresources/listBQ 回空数组但 BQresources/readBQ 报 -32601，自相矛盾） | 7 个（5 静态 + 2 模板）并声明 capabilities | 客户端可直接浏览端点对照表与概览 |\n"
    "| 结果结构 | 只有 content[0].text 的 JSON 字符串 | 同时给 structuredContent + outputSchema | 模型不必二次解析文本 |\n"
    "| MCP logging | 不发 logging/message（BQlogging/setLevelBQ 是空壳） | 同样不发：该能力在协议 2026-07-28 已标记废弃（SEP-2577）；日志统一 stderr | 避免 deprecated 警告与两套日志 |\n"
    "| note/message/reason 上限 | 500 | **200** | 上游 services/org_join_service.py:65 MAX_NOTE=200；v0 的 500 会让模型写出 400 |\n"
    "| object_id 类型 | BQ[\"string\",\"integer\"]BQ 且 pattern 对整数无效 | 一律 str + pattern | union 类型易被客户端拒；上游只按字符串取路径段 |\n"
    "| 永久删除开关 | 无（硬编码不封装，并有测试锁死） | 同样硬编码不封装（测试继续锁死） | 一致 |\n"
)


def to_tool_endpoint(path: str) -> str:
    return (path
            .replace("<int:doc_id>", "{doc_id}")
            .replace("<int:request_id>", "{request_id}")
            .replace("<int:rev>", "{rev}")
            .replace("<object_type>", "{object_type}")
            .replace("<object_id>", "{object_id}")
            .replace("<cid>", "{cid}")
            .replace("<slug>", "{slug}"))


def _short(value: str) -> str:
    return {"integer": "int", "string": "str", "boolean": "bool", "object": "obj",
            "array": "list", "number": "num", "null": "null"}.get(value, value)


def _type_text(prop: dict) -> str:
    if "anyOf" in prop:
        parts = [_short(item.get("type", "?")) for item in prop["anyOf"]]
        base = " 或 ".join(dict.fromkeys(parts))
    else:
        base = _short(prop.get("type", "any"))
    if prop.get("type") == "array":
        items = prop.get("items") or {}
        if "anyOf" in items:
            inner = " 或 ".join(dict.fromkeys(_short(x.get("type", "?")) for x in items["anyOf"]))
        else:
            inner = _short(items.get("type", "any"))
        base += " of " + inner
    if "enum" in prop:
        base += "（枚举：" + "/".join(str(v) for v in prop["enum"]) + "）"
    return base


def _clean(text: str) -> str:
    return text.replace("\n", " ").replace("|", "/").strip()


def _constraint_text(prop: dict) -> str:
    bits: list[str] = []
    if isinstance(prop.get("description"), str):
        bits.append(_clean(prop["description"]))
    nodes = [prop] + list(prop.get("anyOf") or [])
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if isinstance(node.get("description"), str) and node is not prop:
            bits.append(_clean(node["description"]))
        if "enum" in node and node is not prop:
            bits.append("枚举：" + "/".join(str(v) for v in node["enum"]))
        for key, label in (("minLength", "长度≥"), ("maxLength", "长度≤"), ("minimum", "≥"),
                           ("maximum", "≤"), ("minItems", "元素≥"), ("maxItems", "元素≤")):
            if key in node:
                bits.append("%s%s" % (label, node[key]))
        if "pattern" in node:
            bits.append("pattern=" + node["pattern"])
    return "；".join(dict.fromkeys([bit for bit in bits if bit]))


def build_sections() -> tuple[str, str]:
    import asyncio

    env = {"GWL_USER_ID": "1", "GWL_API_KEY": "gk_" + "x" * 43, "GWL_LOG_LEVEL": "CRITICAL"}
    server = create_server(Config.from_env(env))
    tools = asyncio.run(server.list_tools())
    by_name = {tool.name: tool for tool in tools}

    quick = ["| 工具 | 上游端点 | 只读 | 破坏性 | 幂等 |", "|---|---|---|---|---|"]
    detail: list[str] = []
    for spec in ordered(REGISTRY):
        quick.append("| %s | %s | %s | %s | %s |" % (
            spec.name, BQ + spec.endpoint + BQ,
            "✓" if spec.read_only else "", "✓" if spec.destructive else "",
            "✓" if spec.idempotent else ""))
        tool = by_name[spec.name]
        schema = tool.input_schema
        props = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        detail.append("#### %s — %s" % (spec.name, spec.title))
        detail.append("")
        detail.append("上游：**%s**；readOnlyHint=%s，destructiveHint=%s，idempotentHint=%s。"
                      % (BQ + spec.endpoint + BQ, spec.read_only, spec.destructive, spec.idempotent))
        detail.append("")
        detail.append("描述：" + _clean(spec.description))
        detail.append("")
        if not props:
            detail.append("参数：无。")
        else:
            detail.append("| 参数 | 类型 | 必填 | 默认 | 约束与说明 |")
            detail.append("|---|---|---|---|---|")
            for name, prop in props.items():
                detail.append("| %s | %s | %s | %s | %s |" % (
                    name, _type_text(prop), "是" if name in required else "否",
                    "—" if "default" not in prop else json.dumps(prop["default"], ensure_ascii=False),
                    _constraint_text(prop)))
        detail.append("")
    return "\n".join(quick), "\n".join(detail)


def render() -> str:
    covered = {spec.endpoint for spec in REGISTRY}
    by_endpoint: dict[str, list[str]] = {}
    for spec in REGISTRY:
        by_endpoint.setdefault(spec.endpoint, []).append(spec.name)

    rows = []
    for index, (method, path, purpose, line_no) in enumerate(ROUTES, start=1):
        key = "%s %s" % (method, to_tool_endpoint(path))
        names = by_endpoint.get(key, [])
        tools_cell = ", ".join(BQ + name + BQ for name in names) or "—"
        if not names and key != "DELETE /documents/id/{doc_id}/permanent":
            tools_cell = "**未覆盖**"
        if not names and key == "DELETE /documents/id/{doc_id}/permanent":
            tools_cell = "（刻意不封装）"
        rows.append("| %d | %s | %s | %s | %s | %s |"
                    % (index, method, BQ + path + BQ, purpose, tools_cell, line_no))

    quick, detail = build_sections()
    text = (HEADER + "\n".join(rows) + FOOTER + quick + TABLE_END + detail + DIFF_SECTION)
    return text.replace("BQ", BQ)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stdout", action="store_true", help="只打印，不写文件")
    args = parser.parse_args(argv)
    text = render() + "\n"
    if args.stdout:
        sys.stdout.write(text)
        return 0
    target = V1 / "docs" / "API-INVENTORY.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    print("写入 %s（%d 行）" % (target, text.count("\n") + 1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
