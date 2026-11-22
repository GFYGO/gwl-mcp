"""文档与客户端配置模板的一致性测试（不需要上游、不需要凭据）。

覆盖三件事：

1. configs/*.json 全是合法 JSON、结构符合各家客户端的约定，且**不含真实密钥**；
2. .env.example 覆盖了 gwl_mcp/config.py 真正读取的每一个 GWL_* 变量；
3. README.md 与 docs/API-INVENTORY.md 没有和代码脱节（工具名、端点、变量名）。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from gwl_mcp import registry
from gwl_mcp.config import DEFAULT_BASE_URL

V1 = Path(__file__).resolve().parent.parent
CONFIG_DIR = V1 / "configs"

#: 真实 key 的形状：gk_ + 36 位以上字母数字，且不是明显的占位/测试串
REAL_KEY_RE = re.compile(r"gk_(?!x{8,}|TESTONLY|LOCAL)[A-Za-z0-9]{36,}")


def iter_servers(data: dict):
    """各家客户端的 mcpServers 形态不同：对象（Claude/Cursor/VS Code/Cline）、
    对象数组（Continue 的 servers 字段）都要能吃下。"""
    raw = data.get("mcpServers")
    if raw is None:
        raw = data.get("servers") or data.get("mcp") or []
    if isinstance(raw, dict):
        yield from raw.items()
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                yield item.get("name", "unnamed"), item


def config_files() -> list[Path]:
    return sorted(CONFIG_DIR.glob("*.json"))


# ------------------------------------------------------------------ 配置模板
def test_config_dir_has_templates():
    assert len(config_files()) >= 8, "configs/ 下的客户端模板太少"


@pytest.mark.parametrize("path", config_files(), ids=lambda p: p.name)
def test_each_config_is_valid_json(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), path.name + " 顶层应为对象"
    assert "_readme" in data, path.name + " 缺少 _readme 自述字段"


@pytest.mark.parametrize("path", config_files(), ids=lambda p: p.name)
def test_no_real_credentials_in_configs(path: Path):
    raw = path.read_text(encoding="utf-8")
    m = REAL_KEY_RE.search(raw)
    assert m is None, path.name + " 疑似含真实 API Key：" + m.group(0)[:12] + "..."


def test_stdio_configs_reference_run_py():
    """本地 stdio 模板必须能直接跑：命令 + run.py + --transport stdio + 身份 env；

    mcp-remote 桥接模板例外：它启的是 npx，鉴权靠 --header 里的端点令牌。
    """
    checked = 0
    bridged = 0
    for path in config_files():
        data = json.loads(path.read_text(encoding="utf-8"))
        for name, cfg in iter_servers(data):
            if not isinstance(cfg, dict) or cfg.get("url"):
                continue
            args = " ".join(cfg.get("args", []))
            if "mcp-remote" in args:
                bridged += 1
                assert "--header" in args, name + " 桥接配置没有传鉴权 header"
                assert "/mcp" in args, name + " 桥接配置没指向 Streamable HTTP 端点"
                continue
            checked += 1
            assert "run.py" in args, name + " 的 stdio args 未指向 run.py"
            assert "--transport stdio" in args, name + " 未显式声明 stdio"
            env = cfg.get("env") or {}
            assert "GWL_USER_ID" in env and "GWL_API_KEY" in env, name + " env 缺少身份项"
    assert checked >= 4, "至少应有 4 份本地 stdio 模板，实际 " + str(checked)
    assert bridged >= 1, "缺少 mcp-remote 桥接模板"


def test_http_configs_point_at_endpoints():
    urls = []
    for path in config_files():
        data = json.loads(path.read_text(encoding="utf-8"))
        for _name, cfg in iter_servers(data):
            if isinstance(cfg, dict) and cfg.get("url"):
                urls.append(cfg["url"])
    assert any("/mcp" in u for u in urls), "缺少 Streamable HTTP 端点示例"
    assert any("back.gwl.net.cn" in u for u in urls), "缺少托管端点示例"


def test_gen_configs_renders_every_template(tmp_path: Path):
    """渲染脚本把占位符换成可用配置（模拟用户第一次接入）。"""
    out = tmp_path / "generated"
    r = subprocess.run(
        [sys.executable, str(V1 / "scripts" / "gen_configs.py"), "--out", str(out),
         "--user-id", "12", "--python", "python"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stdout + r.stderr
    rendered = sorted(out.glob("*.json"))
    assert len(rendered) == len(config_files()), "渲染份数与模板数不一致"
    for path in rendered:
        text = path.read_text(encoding="utf-8")
        json.loads(text)
        assert "<PYTHON_EXE>" not in text and "<V1_DIR>" not in text, path.name + " 占位符未替换"
        assert "<GWL_USER_ID>" not in text and "<GWL_API_KEY>" not in text, path.name + " 身份占位符未替换"


# ------------------------------------------------------------------ 变量文档
def test_env_example_documents_every_variable():
    source = (V1 / "gwl_mcp" / "config.py").read_text(encoding="utf-8")
    used = set(re.findall(r"(GWL_[A-Z_]+)", source))
    assert used, "config.py 里没解析到环境变量名"
    example = (V1 / ".env.example").read_text(encoding="utf-8")
    missing = sorted(v for v in used if v not in example)
    assert not missing, ".env.example 缺少这些变量：" + ", ".join(missing)


def test_default_api_base_matches_inventory():
    doc = (V1 / "docs" / "API-INVENTORY.md").read_text(encoding="utf-8")
    assert DEFAULT_BASE_URL in doc, "清单里没有默认上游地址"


# ------------------------------------------------------------------ 正文文档
def test_readme_mentions_every_tool():
    readme = (V1 / "README.md").read_text(encoding="utf-8")
    missing = [n for n in registry.names() if n not in readme]
    assert not missing, "README 未列出的工具：" + ", ".join(missing)


def test_inventory_lists_every_tool():
    doc = (V1 / "docs" / "API-INVENTORY.md").read_text(encoding="utf-8")
    missing = [n for n in registry.names() if n not in doc]
    assert not missing, "API-INVENTORY.md 缺少的工具：" + ", ".join(missing)


def test_inventory_lists_every_endpoint():
    doc = (V1 / "docs" / "API-INVENTORY.md").read_text(encoding="utf-8")
    missing = []
    for spec in registry.REGISTRY:
        endpoint = spec.endpoint
        if endpoint and endpoint not in doc:
            missing.append(endpoint)
    assert not missing, "API-INVENTORY.md 缺少的端点：" + ", ".join(missing)


def test_no_real_key_anywhere_in_source_tree():
    """包目录与测试目录里只允许出现测试专用假 key。"""
    offenders = []
    for base in (V1 / "gwl_mcp", V1 / "tests", V1 / "docs"):
        for path in base.rglob("*"):
            if path.is_dir() or path.suffix not in {".py", ".md", ".json", ".toml", ".txt"}:
                continue
            m = REAL_KEY_RE.search(path.read_text(encoding="utf-8", errors="replace"))
            if m:
                offenders.append(path.relative_to(V1).as_posix() + " -> " + m.group(0)[:12])
    assert not offenders, "疑似真实 key：" + "; ".join(offenders)