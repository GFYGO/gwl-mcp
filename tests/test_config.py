"""Config：环境变量校验与凭据安全。"""
from __future__ import annotations

import pytest

from gwl_mcp.config import Config
from gwl_mcp.errors import ConfigError

KEY = "gk_" + "x" * 40


def env(**over):
    base = {"GWL_USER_ID": "12", "GWL_API_KEY": KEY}
    base.update({k: str(v) for k, v in over.items()})
    return base


def test_requires_credentials():
    with pytest.raises(ConfigError, match="GWL_USER_ID"):
        Config.from_env({})
    with pytest.raises(ConfigError, match="GWL_API_KEY"):
        Config.from_env({"GWL_USER_ID": "12"})


def test_userid_must_be_positive_int():
    for bad in ("abc", "0", "-3", "1.5", "12 x"):
        with pytest.raises(ConfigError):
            Config.from_env(env(GWL_USER_ID=bad))


def test_api_key_length_gate_matches_upstream():
    # 上游 MIN/MAX_KEY_LENGTH = 16..200（utils/api_v1.py）
    with pytest.raises(ConfigError, match="长度"):
        Config.from_env(env(GWL_API_KEY="gk_short"))
    with pytest.raises(ConfigError, match="长度"):
        Config.from_env(env(GWL_API_KEY="gk_" + "x" * 200))


def test_base_url_normalisation():
    cfg = Config.from_env(env(GWL_API_BASE="https://back.gwl.net.cn"))
    assert cfg.base_url == "https://back.gwl.net.cn/api/v1"
    cfg2 = Config.from_env(env(GWL_API_BASE="https://example.com/api/v1/"))
    assert cfg2.base_url == "https://example.com/api/v1"
    with pytest.raises(ConfigError, match="query"):
        Config.from_env(env(GWL_API_BASE="https://example.com/api/v1?apikey=abc"))


def test_http_base_requires_opt_in():
    with pytest.raises(ConfigError, match="GWL_ALLOW_HTTP"):
        Config.from_env(env(GWL_API_BASE="http://127.0.0.1:5000/api/v1"))
    cfg = Config.from_env(env(GWL_API_BASE="http://127.0.0.1:5000/api/v1", GWL_ALLOW_HTTP="1"))
    assert cfg.base_url.startswith("http://")


def test_org_class_validation():
    assert Config.from_env(env(GWL_ORG_CLASS="DEV")).org_class == "dev"
    with pytest.raises(ConfigError):
        Config.from_env(env(GWL_ORG_CLASS="toolong"))


def test_total_timeout_must_exceed_timeout():
    with pytest.raises(ConfigError, match="GWL_TOTAL_TIMEOUT"):
        Config.from_env(env(GWL_TIMEOUT="20", GWL_TOTAL_TIMEOUT="10"))


def test_public_bind_requires_token():
    with pytest.raises(ConfigError, match="GWL_HTTP_AUTH_TOKEN"):
        Config.from_env(env(GWL_HTTP_HOST="0.0.0.0"))
    cfg = Config.from_env(env(GWL_HTTP_HOST="0.0.0.0", GWL_HTTP_AUTH_TOKEN="a" * 32))
    assert cfg.http_host == "0.0.0.0"
    # 明确知道自己风险的逃生口
    cfg2 = Config.from_env(env(GWL_HTTP_HOST="0.0.0.0", GWL_HTTP_ALLOW_INSECURE="1"))
    assert cfg2.http_auth_token is None


def test_token_min_length():
    with pytest.raises(ConfigError, match="16"):
        Config.from_env(env(GWL_HTTP_AUTH_TOKEN="short"))


def test_credentials_never_in_repr_or_summary(fake_gwl):
    cfg = Config.from_env(dict(fake_gwl.env))
    assert fake_gwl.env["GWL_API_KEY"] not in repr(cfg)
    summary = cfg.redacted_summary()
    assert fake_gwl.env["GWL_API_KEY"] not in str(summary)
    assert summary["api_key_present"] is True


def test_env_file_precedence(tmp_path):
    env_file = tmp_path / "x.env"
    env_file.write_text("GWL_USER_ID=7\nGWL_API_KEY=" + KEY + "\n# comment\n", encoding="utf-8")
    cfg = Config.from_env(env={}, env_file=str(env_file))
    assert cfg.user_id == 7
    cfg2 = Config.from_env(env={"GWL_USER_ID": "9", "GWL_API_KEY": KEY, "GWL_ENV_FILE": str(env_file)})
    assert cfg2.user_id == 9  # 显式环境变量优先于 .env
