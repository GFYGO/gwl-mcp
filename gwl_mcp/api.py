"""底层 GWL /api/v1 HTTP 客户端（标准库 urllib + 线程化异步包装）。

契约（逐条对着 site-back 的实现核对，来源见行号注释）：

* 凭据只走请求头 X-User-Id / X-Api-Key（utils/api_v1.py::_header_credentials），
  绝不拼进 URL、绝不写进日志；
* 写请求的业务参数统一包成 {"data": {...}}（utils/api_v1.py::resolve_payload 第一优先级），
  并且丢掉 None —— 上游用 data.get(k) or default 读参数，传 null 等价于不传；
* GET 的业务参数走 query string，bool 转 1/0，list 转逗号分隔（上游按 CSV 解析）；
* 禁止跟随重定向：认证头不能跟着 3xx 跑到别的主机；
* 上游包络是 {code, msg, data}，且 code 恒等于 HTTP 状态码（utils/response.py::success/fail），
  所以只判 HTTP 状态是不够的，code != 200 同样必须当失败；
* 重试策略不对称：429（限流发生在业务执行之前，重放安全）任何方法都重试；
  5xx 与连接错误只重试 GET。POST/PUT/DELETE 遇到不确定的失败一律不重放 ——
  上游没有幂等键，重复 POST /documents 会真的多出文档，
  重复 DELETE /org/join-requests/<id> 会把已成功的操作变成 409/404 假错误。
"""
from __future__ import annotations

import json
import logging
import random
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import anyio

from .config import Config
from .errors import UpstreamError, build_api_error_message

LOG = logging.getLogger("gwl_mcp.api")

#: 只有幂等的读请求才允许在网络层失败后重放
REPLAYABLE_METHODS = ("GET",)
#: 值得重试的上游状态码（网关 / 服务抖动）
RETRYABLE_STATUSES = (500, 502, 503, 504)
#: Retry-After 等待上下限（秒）
RETRY_AFTER_MIN = 1
RETRY_AFTER_MAX = 30

USER_AGENT = "gwl-mcp/2.0.0 (+https://gwl.net.cn)"


@dataclass(frozen=True)
class ApiResponse:
    """一次成功的上游调用。"""

    status: int
    code: int
    msg: str
    data: Any
    rate_limit: dict[str, Any] = field(default_factory=dict)
    attempts: int = 1
    elapsed_ms: int = 0

    def envelope(self) -> dict[str, Any]:
        """工具返回的统一结构：模型能直接读懂，并带上限流余额。"""
        payload: dict[str, Any] = {"ok": True, "msg": self.msg, "data": self.data}
        if self.rate_limit:
            payload["rate_limit"] = self.rate_limit
        return payload


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """拒绝 3xx：避免把 API Key 带到其它主机。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise urllib.error.HTTPError(
            req.full_url, code, "重定向被拒绝（避免 API Key 泄露到其它主机）", headers, fp
        )


def build_opener() -> Any:
    context = ssl.create_default_context()
    return urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=context))


class ApiClient:
    """同步客户端；MCP 工具用 arequest()（线程池包装，不阻塞事件循环）。"""

    def __init__(self, config: Config, *, opener: Any = None, sleep: Any = time.sleep):
        self.config = config
        self._opener = opener or build_opener()
        self._sleep = sleep

    # ------------------------------------------------------------------
    # 便捷入口
    # ------------------------------------------------------------------
    def get(self, path: str, query: dict[str, Any] | None = None) -> ApiResponse:
        return self.request("GET", path, query=query)

    def post(self, path: str, body: dict[str, Any] | None = None) -> ApiResponse:
        return self.request("POST", path, body=body)

    def put(self, path: str, body: dict[str, Any] | None = None) -> ApiResponse:
        return self.request("PUT", path, body=body)

    def delete(self, path: str, body: dict[str, Any] | None = None,
               query: dict[str, Any] | None = None) -> ApiResponse:
        return self.request("DELETE", path, query=query, body=body)

    async def arequest(self, method: str, path: str, *,
                       query: dict[str, Any] | None = None,
                       body: dict[str, Any] | None = None) -> ApiResponse:
        return await anyio.to_thread.run_sync(
            lambda: self.request(method, path, query=query, body=body)
        )

    # ------------------------------------------------------------------
    # 核心：带总预算与退避的重试循环
    # ------------------------------------------------------------------
    def request(self, method: str, path: str, *,
                query: dict[str, Any] | None = None,
                body: dict[str, Any] | None = None) -> ApiResponse:
        method = method.upper()
        url = self.build_url(path, query)
        log_path = urllib.parse.urlsplit(url).path
        started = time.monotonic()
        deadline = started + self.config.total_timeout
        attempt = 0

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise UpstreamError(
                    "GWL API %s %s 超出单次调用的总时间预算 %s 秒（已尝试 %d 次）。"
                    "请检查网络，或调大 GWL_TOTAL_TIMEOUT / GWL_TIMEOUT。"
                    % (method, log_path, int(self.config.total_timeout), attempt),
                    status=None,
                    attempts=attempt + 1,
                )
            timeout = min(self.config.timeout, remaining)
            try:
                response = self._send(method, url, body, timeout=timeout)
                elapsed = int((time.monotonic() - started) * 1000)
                if attempt:
                    LOG.info("GWL API %s %s 第 %d 次尝试成功（%d ms）",
                             method, log_path, attempt + 1, elapsed)
                return ApiResponse(
                    status=response.status,
                    code=response.code,
                    msg=response.msg,
                    data=response.data,
                    rate_limit=response.rate_limit,
                    attempts=attempt + 1,
                    elapsed_ms=elapsed,
                )
            except UpstreamError as exc:
                attempt += 1
                if not self._should_retry(method, exc) or attempt > self.config.max_retries:
                    exc.attempts = attempt
                    raise
                delay = self._retry_delay(attempt - 1, exc)
                if time.monotonic() + delay >= deadline:
                    exc.attempts = attempt
                    raise
                LOG.warning(
                    "GWL API %s %s 失败（HTTP %s），%.1f 秒后重试（第 %d/%d 次）",
                    method, log_path, exc.status, delay, attempt, self.config.max_retries,
                )
                self._sleep(delay)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                attempt += 1
                reason = _safe_reason(exc)
                if method not in REPLAYABLE_METHODS or attempt > self.config.max_retries:
                    raise UpstreamError(
                        "无法连接 GWL API（%s）：%s。请检查网络、代理与 GWL_API_BASE。"
                        % (self.config.base_url, reason),
                        status=None,
                        attempts=attempt,
                    ) from exc
                delay = min(2 ** (attempt - 1), 8) + random.uniform(0, 0.25)
                if time.monotonic() + delay >= deadline:
                    raise UpstreamError(
                        "无法连接 GWL API（%s）：%s（总时间预算已用尽）。"
                        % (self.config.base_url, reason),
                        status=None,
                        attempts=attempt,
                    ) from exc
                LOG.warning(
                    "GWL API %s %s 网络错误：%s，%.1f 秒后重试（第 %d/%d 次）",
                    method, log_path, reason, delay, attempt, self.config.max_retries,
                )
                self._sleep(delay)

    # ------------------------------------------------------------------
    # 单次发送
    # ------------------------------------------------------------------
    def _send(self, method: str, url: str, body: dict[str, Any] | None, *,
              timeout: float) -> ApiResponse:
        headers = {
            "X-User-Id": str(self.config.user_id),
            "X-Api-Key": self.config.api_key,
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        if self.config.org_class:
            headers["X-Org-Class"] = self.config.org_class

        payload_bytes = None
        if body is not None and method != "GET":
            # None 字段不下发：上游把 null 与「没传」同等对待（data.get(k) or default）
            payload_bytes = json.dumps({"data": compact(body)}, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"

        request = urllib.request.Request(url, data=payload_bytes, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=timeout) as response:
                raw = response.read()
                status = int(response.status)
                resp_headers = response.headers
        except urllib.error.HTTPError as exc:
            if int(exc.code) in (301, 302, 303, 307, 308):
                location = ""
                try:
                    location = str(exc.headers.get("Location") or "") if exc.headers else ""
                except Exception:  # noqa: BLE001
                    location = ""
                raise UpstreamError(
                    "上游返回了重定向（HTTP %s，Location=%s）。本客户端拒绝跟随重定向，"
                    "以免把 API Key 带到其它主机；请把 GWL_API_BASE 直接改成最终地址。"
                    % (exc.code, location or "?"),
                    status=int(exc.code),
                ) from exc
            try:
                raw = exc.read()
            except Exception:  # noqa: BLE001
                raw = b""
            finally:
                try:
                    exc.close()
                except Exception:  # noqa: BLE001
                    pass
            status = int(exc.code)
            resp_headers = exc.headers
        except (urllib.error.URLError, TimeoutError, OSError):
            raise

        text = _decode(raw, resp_headers)
        content_type = str(resp_headers.get("Content-Type") or "") if resp_headers else ""
        retry_after = _parse_retry_after(resp_headers)
        rate_limit = _rate_limit(resp_headers)

        parsed: Any = None
        parse_failed = False
        if text.strip():
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parse_failed = True
        if not isinstance(parsed, dict):
            parse_failed = True

        if parse_failed or not (200 <= status < 300):
            msg = str(parsed.get("msg") or "") if isinstance(parsed, dict) else ""
            code = parsed.get("code", status) if isinstance(parsed, dict) else status
            data = parsed.get("data") if isinstance(parsed, dict) else None
            raise UpstreamError(
                build_api_error_message(
                    status, msg,
                    code=code,
                    data=data,
                    retry_after=retry_after,
                    non_json=parse_failed,
                    content_type=content_type,
                    preview=text,
                    base_url=self.config.base_url,
                ),
                status=status,
                code=code,
                data=data,
                retry_after=retry_after,
                non_json=parse_failed,
                content_type=content_type,
            )

        try:
            code_int = int(parsed.get("code"))
        except (TypeError, ValueError) as exc:
            raise UpstreamError(
                "GWL API 响应里的业务 code 不可解析（HTTP %s，原始值 %r）。"
                % (status, parsed.get("code")),
                status=status,
            ) from exc

        if code_int != 200:
            # 上游正常情况下 code == HTTP status；这一分支兜住任何不一致
            raise UpstreamError(
                build_api_error_message(
                    status, str(parsed.get("msg") or ""),
                    code=parsed.get("code"),
                    data=parsed.get("data"),
                    retry_after=retry_after,
                    base_url=self.config.base_url,
                ),
                status=status,
                code=parsed.get("code"),
                data=parsed.get("data"),
                retry_after=retry_after,
            )

        LOG.debug("GWL API %s %s -> HTTP %s", method, urllib.parse.urlsplit(url).path, status)
        return ApiResponse(
            status=status,
            code=code_int,
            msg=str(parsed.get("msg") or "成功"),
            data=parsed.get("data"),
            rate_limit=rate_limit,
        )

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    def build_url(self, path: str, query: dict[str, Any] | None) -> str:
        if not path.startswith("/"):
            raise ValueError("API path 必须以 / 开头")
        url = self.config.base_url + path
        pairs = encode_query(query)
        if pairs:
            url += "?" + urllib.parse.urlencode(pairs)
        return url

    @staticmethod
    def _should_retry(method: str, exc: UpstreamError) -> bool:
        if exc.status == 429:
            return True
        if method in REPLAYABLE_METHODS and (exc.status in RETRYABLE_STATUSES or exc.status is None):
            return True
        return False

    @staticmethod
    def _retry_delay(attempt: int, exc: UpstreamError) -> float:
        if exc.retry_after:
            return float(max(RETRY_AFTER_MIN, min(int(exc.retry_after), RETRY_AFTER_MAX)))
        return float(min(2 ** attempt, 8)) + random.uniform(0, 0.25)


def encode_query(query: dict[str, Any] | None) -> list[tuple[str, str]]:
    """query 组装约定：None 丢弃、bool 转 1/0、list/tuple 转逗号分隔。"""
    pairs: list[tuple[str, str]] = []
    for key, value in (query or {}).items():
        if value is None:
            continue
        if isinstance(value, bool):
            value = "1" if value else "0"
        elif isinstance(value, (list, tuple)):
            value = ",".join(str(item) for item in value)
        pairs.append((str(key), str(value)))
    return pairs


def compact(body: dict[str, Any]) -> dict[str, Any]:
    """丢掉 None 字段：上游把 null 与没传同等对待，不如不传。"""
    return {key: value for key, value in body.items() if value is not None}


def _decode(raw: bytes, headers: Any) -> str:
    charset = "utf-8"
    try:
        if headers is not None:
            charset = headers.get_content_charset() or "utf-8"
    except Exception:  # noqa: BLE001
        pass
    try:
        return raw.decode(charset, errors="replace")
    except (LookupError, UnicodeError):
        return raw.decode("utf-8", errors="replace")


def _rate_limit(headers: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, name in (
        ("X-RateLimit-Limit", "limit"),
        ("X-RateLimit-Remaining", "remaining"),
        ("X-RateLimit-Reset", "reset"),
        ("Retry-After", "retry_after"),
    ):
        raw = headers.get(key) if headers is not None else None
        if raw is None:
            continue
        try:
            out[name] = int(str(raw).strip())
        except (TypeError, ValueError):
            out[name] = str(raw)
    return out


def _parse_retry_after(headers: Any) -> int | None:
    """上游 _rate_failure 发秒数，网关可能发 HTTP-date，两种都支持。"""
    raw = headers.get("Retry-After") if headers is not None else None
    if raw is None:
        return None
    text = str(raw).strip()
    try:
        return max(0, int(text))
    except (TypeError, ValueError):
        pass
    try:
        when = parsedate_to_datetime(text)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0, int((when - datetime.now(timezone.utc)).total_seconds()))
    except (TypeError, ValueError, OverflowError):
        return None


def _safe_reason(exc: BaseException) -> str:
    reason = getattr(exc, "reason", None)
    text = str(reason if reason is not None else exc)
    return text if len(text) <= 200 else text[:200] + "…"