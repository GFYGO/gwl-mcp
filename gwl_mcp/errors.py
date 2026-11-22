"""异常类型与面向模型 / 用户的错误文案。

约定（与 v0 一致，且都对着 site-back 的实现核对过）：

* 所有可预期错误继承 GwlError，message 可以直接展示给 MCP 客户端；
* 错误信息里绝不出现 API Key、请求头或请求体；
* ApiError 额外保留 HTTP 状态、业务 code、限流与冲突详情，供工具层生成结构化错误；
* 工具层把 GwlError 转成 MCP 的 isError=true 结果（模型可读），
  而不是 JSON-RPC 协议错误 —— 协议错误只留给真正的协议层问题。
"""
from __future__ import annotations

from typing import Any


class GwlError(Exception):
    """可预期错误基类。"""

    def __init__(self, message: str, *, detail: Any = None):
        super().__init__(message)
        self.message = message
        self.detail = detail

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"ok": False, "message": self.message}
        if self.detail is not None:
            payload["detail"] = self.detail
        return payload


class ConfigError(GwlError):
    """配置缺失或非法（缺 GWL_USER_ID / GWL_API_KEY / 基址非法等）。"""


class ValidationError(GwlError):
    """工具参数校验失败（未知参数、跨字段约束、空列表等）。"""


class UpstreamError(GwlError):
    """底层 GWL /api/v1 返回错误或网络层失败。"""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: int | str | None = None,
        data: Any = None,
        retry_after: int | None = None,
        non_json: bool = False,
        content_type: str = "",
        attempts: int = 1,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.data = data
        self.retry_after = retry_after
        self.non_json = non_json
        self.content_type = content_type
        self.attempts = attempts

    def to_payload(self) -> dict[str, Any]:
        payload = super().to_payload()
        if self.status is not None:
            payload["http_status"] = self.status
        if self.code is not None:
            payload["api_code"] = self.code
        if self.retry_after is not None:
            payload["retry_after"] = self.retry_after
        if self.data is not None:
            payload["data"] = self.data
        if self.non_json:
            payload["non_json"] = True
            if self.content_type:
                payload["content_type"] = self.content_type
        if self.attempts > 1:
            payload["attempts"] = self.attempts
        return payload


#: 别名：v0 叫 ApiError，代码与测试里两个名字都可能出现
ApiError = UpstreamError


def build_api_error_message(
    status: int | None,
    msg: str,
    *,
    code: int | str | None = None,
    data: Any = None,
    retry_after: int | None = None,
    non_json: bool = False,
    content_type: str = "",
    preview: str = "",
    base_url: str = "",
) -> str:
    """把上游错误翻译成可操作的中文提示（不包含密钥）。

    状态码 / 业务语义来源（不要凭印象改）：
    * 401 缺少或错用凭据 —— site-back/utils/api_v1.py::_auth_failure
    * 403 账户锁定 / 权限不足 —— site-back/utils/api_v1.py::_locked_response
    * 429 限流（读 120 / 写 30 / IP 240 / 认证失败 20，每分钟）
      —— site-back/utils/api_v1.py::RATE_LIMIT_*
    * 409 文档乐观锁冲突 —— data 里带 current_version / expected_version
    * 413 请求体上限 16MB
    """
    if non_json:
        head = "GWL API 返回了非 JSON 响应"
        if status is not None:
            head += "（HTTP %s）" % status
        if content_type:
            head += "，Content-Type: %s" % content_type
        body = (preview or "").replace("\r", " ").replace("\n", " ").strip()
        if len(body) > 200:
            body = body[:200] + "…"
        text = head + "。"
        if body:
            text += "响应片段：%s。" % body
        text += "这通常意味着网关没有把 /api/v1 转发到后端（或返回了 HTML 错误页）。"
        text += "请先确认 GWL_API_BASE=%s 与线上反向代理配置。" % (base_url or "（未设置）")
        return text

    message = (msg or "上游 API 返回错误").strip()
    if code is not None and str(code) != str(status):
        message += "（业务 code=%s）" % code
    if status is not None:
        message += "（HTTP %s）" % status

    if status == 400:
        message += "。请检查参数类型、必填项与资源限制。"
    elif status == 401:
        message += (
            "。请检查 GWL_USER_ID 与 GWL_API_KEY 是否匹配；"
            "key 轮换或撤销后旧 key 会立即失效（key 只走请求头，不会进 URL 与日志）。"
        )
    elif status == 403:
        message += "。当前账号对目标资源没有权限（或账户被锁定）；请确认文档可见性、组归属或管理角色。"
    elif status == 404:
        message += "。请检查 doc_id / slug / cid / 申请编号是否存在且对你可见。"
    elif status == 409:
        message += "。目标状态已变化（文档版本冲突、申请已处理、用户已在黑名单等）。"
        if isinstance(data, dict):
            pieces = []
            for key, label in (
                ("current_version", "当前版本"),
                ("expected_version", "提交版本"),
                ("editor_username", "最后编辑者"),
                ("doc_id", "文档 id"),
            ):
                if data.get(key) is not None:
                    pieces.append("%s=%s" % (label, data[key]))
            if pieces:
                message += " " + "，".join(pieces) + "。请先重新读取文档再提交。"
    elif status == 413:
        message += "。请求体超过 16MB 上限；请拆分文档或缩小内容。"
    elif status == 429:
        message += "。触发上游限流：读 120 次/分钟、写 30 次/分钟、单 IP 240 次/分钟。"
        if retry_after:
            message += " 已按 Retry-After 等待过仍失败，建议再等 %d 秒。" % retry_after
        else:
            message += " 请降低调用频率后重试。"
    elif status is not None and status >= 500:
        message += "。上游服务暂时不可用；只读请求已按退避重试，仍失败请稍后再试。"
    return message
