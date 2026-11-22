"""配置读取与校验。

所有凭据只从环境变量（或显式指定的 .env 文件）读取，绝不硬编码、绝不写进示例配置。
变量清单见 ../.env.example 与 ../README.md。

与 v0 的兼容性：变量名与语义完全沿用 v0（GWL_*），因此同一份 .env 可以两边共用。
永久删除接口（DELETE /documents/id/<id>/permanent）在 v1 里同样**不提供工具**，
因此没有任何「打开不可逆操作」的开关。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .errors import ConfigError

#: 线上 v1 用户 API 基址（site-back/app.py 注册 api_v1_bp 前缀 /api/v1）
DEFAULT_BASE_URL = "https://back.gwl.net.cn/api/v1"
#: 单次 HTTP 超时（秒）。上游读接口最慢的是搜索与文档正文，20s 足够。
DEFAULT_TIMEOUT = 20.0
#: 只读请求与 429 的额外重试次数上限
DEFAULT_MAX_RETRIES = 2
#: 单次工具调用的总墙钟预算（含全部重试与退避），防止把 MCP 客户端挂死
DEFAULT_TOTAL_TIMEOUT = 60.0

DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8765
DEFAULT_HTTP_PATH = "/mcp"
DEFAULT_HTTP_SESSION_TTL = 1800
DEFAULT_HTTP_MAX_BODY_MB = 16

#: 与 utils/api_v1.py::RATE_LIMIT_* 对齐的默认口径（仅用于文档与错误提示）
API_RATE_LIMITS = {
    "read_per_minute": 120,
    "write_per_minute": 30,
    "ip_per_minute": 240,
    "auth_fail_per_minute": 20,
}

_ORG_CLASS_RE = re.compile(r"^[A-Za-z]{3}$")
_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


@dataclass(frozen=True)
class Config:
    """一次 MCP 服务进程的不可变配置。"""

    base_url: str
    user_id: int
    api_key: str = field(repr=False)
    org_class: str | None = None
    timeout: float = DEFAULT_TIMEOUT
    #: 单次工具调用（含全部重试与退避）的总墙钟预算，防止把 MCP 客户端挂死
    total_timeout: float = DEFAULT_TOTAL_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    enable_writes: bool = True
    log_level: str = "WARNING"

    # ---- Streamable HTTP / SSE 传输（服务端） ----
    http_host: str = DEFAULT_HTTP_HOST
    http_port: int = DEFAULT_HTTP_PORT
    http_path: str = DEFAULT_HTTP_PATH
    http_auth_token: str | None = field(default=None, repr=False)
    http_allowed_origins: tuple[str, ...] = ()
    http_session_ttl: int = DEFAULT_HTTP_SESSION_TTL
    #: true 时用 application/json 回应 POST（不升级为 SSE）——
    #: 放在会缓冲 SSE 的反代后面时更稳，代价是拿不到服务端推送。
    http_json_response: bool = False
    http_max_body_mb: int = DEFAULT_HTTP_MAX_BODY_MB
    http_allow_insecure: bool = False

    @classmethod
    def from_env(
        cls,
        env: dict[str, str] | None = None,
        env_file: str | None = None,
    ) -> "Config":
        """从环境变量（可选叠加 .env 文件）构造配置。

        .env 的值先加载，随后被同名显式环境变量覆盖（与 dotenv 语义一致）。
        """
        base = dict(os.environ) if env is None else dict(env)
        path = env_file or base.get("GWL_ENV_FILE")
        if path:
            file_values = read_env_file(path)
            merged = dict(file_values)
            merged.update(base)
            base = merged

        base_url = _normalise_base_url(
            base.get("GWL_API_BASE", DEFAULT_BASE_URL),
            allow_http=_parse_bool(base.get("GWL_ALLOW_HTTP"), default=False),
        )
        user_id = _parse_user_id(base.get("GWL_USER_ID"))
        api_key = _parse_api_key(base.get("GWL_API_KEY"))
        org_class = _parse_org_class(base.get("GWL_ORG_CLASS"))
        timeout = _parse_float(base.get("GWL_TIMEOUT"), DEFAULT_TIMEOUT, 1.0, 120.0, "GWL_TIMEOUT")
        total_timeout = _parse_float(
            base.get("GWL_TOTAL_TIMEOUT"), DEFAULT_TOTAL_TIMEOUT, 5.0, 300.0, "GWL_TOTAL_TIMEOUT"
        )
        if total_timeout < timeout:
            raise ConfigError(
                "GWL_TOTAL_TIMEOUT（%s 秒）不能小于 GWL_TIMEOUT（%s 秒）："
                "否则第一次请求就会被总预算截断。" % (total_timeout, timeout)
            )
        max_retries = _parse_int(base.get("GWL_MAX_RETRIES"), DEFAULT_MAX_RETRIES, 0, 5, "GWL_MAX_RETRIES")
        enable_writes = _parse_bool(base.get("GWL_ENABLE_WRITES"), default=True)
        log_level = str(base.get("GWL_LOG_LEVEL", "WARNING")).strip().upper() or "WARNING"
        if log_level not in _LOG_LEVELS:
            raise ConfigError(
                "GWL_LOG_LEVEL 取值无效：%s（可选 DEBUG/INFO/WARNING/ERROR/CRITICAL）" % log_level
            )

        http_host = _parse_http_host(base.get("GWL_HTTP_HOST"))
        # 允许 0 = 让内核挑随机端口（测试与多实例并行用）
        http_port = _parse_int(base.get("GWL_HTTP_PORT"), DEFAULT_HTTP_PORT, 0, 65535, "GWL_HTTP_PORT")
        http_path = _parse_http_path(base.get("GWL_HTTP_PATH"))
        http_auth_token = _parse_http_token(base.get("GWL_HTTP_AUTH_TOKEN"))
        http_allowed_origins = _parse_allowed_origins(base.get("GWL_HTTP_ALLOWED_ORIGINS"))
        http_session_ttl = _parse_int(
            base.get("GWL_HTTP_SESSION_TTL"), DEFAULT_HTTP_SESSION_TTL, 60, 86400, "GWL_HTTP_SESSION_TTL"
        )
        http_max_body_mb = _parse_int(
            base.get("GWL_HTTP_MAX_BODY_MB"), DEFAULT_HTTP_MAX_BODY_MB, 1, 64, "GWL_HTTP_MAX_BODY_MB"
        )
        http_allow_insecure = _parse_bool(base.get("GWL_HTTP_ALLOW_INSECURE"), default=False)
        http_json_response = _parse_bool(base.get("GWL_HTTP_JSON_RESPONSE"), default=False)

        if http_auth_token is None and not _is_loopback(http_host) and not http_allow_insecure:
            raise ConfigError(
                "HTTP 传输监听在非回环地址 %s 却没有设置 GWL_HTTP_AUTH_TOKEN。"
                "公网/局域网暴露 MCP 端点前必须设置访问令牌（至少 16 字符）；"
                "确认内网可信环境才可设 GWL_HTTP_ALLOW_INSECURE=1。" % http_host
            )

        return cls(
            base_url=base_url,
            user_id=user_id,
            api_key=api_key,
            org_class=org_class,
            timeout=timeout,
            total_timeout=total_timeout,
            max_retries=max_retries,
            enable_writes=enable_writes,
            log_level=log_level,
            http_host=http_host,
            http_port=http_port,
            http_path=http_path,
            http_auth_token=http_auth_token,
            http_allowed_origins=http_allowed_origins,
            http_session_ttl=http_session_ttl,
            http_max_body_mb=http_max_body_mb,
            http_allow_insecure=http_allow_insecure,
            http_json_response=http_json_response,
        )

    def redacted_summary(self) -> dict[str, object]:
        """给 --check / 资源用的安全摘要（永不含 key / token）。"""
        return {
            "base_url": self.base_url,
            "user_id": self.user_id,
            "api_key_present": bool(self.api_key),
            "api_key_length": len(self.api_key),
            "org_class": self.org_class,
            "timeout_seconds": self.timeout,
            "total_timeout_seconds": self.total_timeout,
            "max_retries": self.max_retries,
            "enable_writes": self.enable_writes,
            "log_level": self.log_level,
            "transport": {"http_host": self.http_host, "http_port": self.http_port, "http_path": self.http_path,
                          "auth_token_configured": self.http_auth_token is not None},
        }


def read_env_file(path: str) -> dict[str, str]:
    """读取简单的 KEY=VALUE .env 文件（只接受 GWL_ 前缀，去成对引号）。"""
    file_path = Path(os.path.expanduser(path))
    if not file_path.is_file():
        raise ConfigError("GWL_ENV_FILE 指向的文件不存在：%s" % file_path)
    try:
        text = file_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ConfigError("读取配置文件失败：%s（%s）" % (file_path, exc)) from exc
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key.startswith("GWL_"):
            continue
        if len(value) >= 2 and value[0] in (chr(39), chr(34)) and value[-1] == value[0]:
            value = value[1:-1]
        values[key] = value
    return values


# ----------------------------------------------------------------------
# 单项校验
# ----------------------------------------------------------------------
def _normalise_base_url(raw: object, *, allow_http: bool) -> str:
    text = str(raw or "").strip()
    if not text:
        raise ConfigError("GWL_API_BASE 不能为空")
    if not text.startswith(("http://", "https://")):
        raise ConfigError("GWL_API_BASE 必须以 https:// 或 http:// 开头（收到 %r）" % text)
    parts = urlsplit(text)
    if not parts.netloc:
        raise ConfigError("GWL_API_BASE 缺少主机名：%s" % text)
    if parts.scheme == "http" and not allow_http:
        raise ConfigError(
            "GWL_API_BASE 使用 http 会明文传输 API Key；仅限本地调试，"
            "确认后再显式设置 GWL_ALLOW_HTTP=1"
        )
    if parts.query or parts.fragment:
        raise ConfigError("GWL_API_BASE 不能带 query 或 fragment：%s" % text)
    path = parts.path.rstrip("/")
    if not path.endswith("/api/v1"):
        # 允许 https://back.gwl.net.cn 这种忘了写前缀的写法，自动补齐
        path = path + "/api/v1"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _parse_user_id(raw: object) -> int:
    text = str(raw or "").strip()
    if not text:
        raise ConfigError("缺少 GWL_USER_ID（GWL 站点的数字用户 id，在仪表盘设置里可查看）")
    if not text.isdigit() or int(text) <= 0:
        raise ConfigError("GWL_USER_ID 必须是正整数（收到 %r）" % text)
    if len(text) > 20:
        raise ConfigError("GWL_USER_ID 过长（最多 20 位）")
    return int(text)


def _parse_api_key(raw: object) -> str:
    text = str(raw or "").strip()
    if not text:
        raise ConfigError(
            "缺少 GWL_API_KEY（形如 gk_...，在 GWL 仪表盘「设置 - API Key」里生成；"
            "key 轮换后旧值立即失效）"
        )
    if not (16 <= len(text) <= 200):
        raise ConfigError("GWL_API_KEY 长度必须在 16~200 之间（收到 %d 个字符）" % len(text))
    return text


def _parse_org_class(raw: object) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if _ORG_CLASS_RE.fullmatch(text) is None:
        raise ConfigError("GWL_ORG_CLASS 必须是 3 位字母的组类标识（收到 %r）" % text)
    return text.lower()


def _parse_http_host(raw: object) -> str:
    text = str(raw or "").strip() or DEFAULT_HTTP_HOST
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-:[]")
    bad = sorted({ch for ch in text if ch not in allowed})
    if bad:
        raise ConfigError("GWL_HTTP_HOST 含非法字符 %s：%s" % ("".join(bad), text))
    if any(ch.isspace() for ch in text) or "/" in text:
        raise ConfigError("GWL_HTTP_HOST 不能含空白或斜杠：%s" % text)
    return text


def _is_loopback(host: str) -> bool:
    """只有真正的回环地址算「本机可达」。

    0.0.0.0 / :: 是「监听所有网卡」，等同于暴露到局域网，必须要求访问令牌。
    """
    return host.strip("[]") in ("127.0.0.1", "localhost", "::1")


def _parse_http_path(raw: object) -> str:
    text = str(raw or "").strip() or DEFAULT_HTTP_PATH
    if not text.startswith("/"):
        raise ConfigError("GWL_HTTP_PATH 必须以 / 开头：%s" % text)
    if len(text) > 100 or "?" in text or "#" in text:
        raise ConfigError("GWL_HTTP_PATH 非法：%s" % text)
    return text


def _parse_http_token(raw: object) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if len(text) < 16:
        raise ConfigError("GWL_HTTP_AUTH_TOKEN 至少 16 个字符（当前 %d）" % len(text))
    return text


def _parse_allowed_origins(raw: object) -> tuple[str, ...]:
    text = str(raw or "").strip()
    if not text:
        return ()
    out = []
    for item in text.split(","):
        item = item.strip()
        if item:
            out.append(item)
    return tuple(out)


def _parse_int(raw: object, default: int, minimum: int, maximum: int, name: str) -> int:
    text = str(raw if raw is not None else default).strip()
    if not text:
        return default
    try:
        value = int(text)
    except ValueError as exc:
        raise ConfigError("%s 必须是整数（收到 %r）" % (name, raw)) from exc
    if not minimum <= value <= maximum:
        raise ConfigError("%s 必须在 %d~%d 之间（收到 %d）" % (name, minimum, maximum, value))
    return value


def _parse_float(raw: object, default: float, minimum: float, maximum: float, name: str) -> float:
    text = str(raw if raw is not None else default).strip()
    if not text:
        return default
    try:
        value = float(text)
    except ValueError as exc:
        raise ConfigError("%s 必须是数字（收到 %r）" % (name, raw)) from exc
    if not minimum <= value <= maximum:
        raise ConfigError("%s 必须在 %s~%s 之间（收到 %s）" % (name, minimum, maximum, value))
    return value


def _parse_bool(raw: object, *, default: bool) -> bool:
    text = str(raw if raw is not None else "").strip().lower()
    if not text:
        return default
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    raise ConfigError("布尔型环境变量取值非法：%r（可用 1/0、true/false）" % raw)