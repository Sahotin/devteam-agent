from __future__ import annotations

from urllib.parse import urlparse

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
)


class ModelServiceError(RuntimeError):
    """可安全展示给用户的模型服务错误。"""

    code = "MODEL_SERVICE_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(f"{self.code} | {message}")


class ModelConnectionError(ModelServiceError):
    code = "MODEL_CONNECTION_ERROR"


class ModelTimeoutError(ModelServiceError):
    code = "MODEL_TIMEOUT_ERROR"


class ModelAuthenticationError(ModelServiceError):
    code = "MODEL_AUTHENTICATION_ERROR"


class ModelRateLimitError(ModelServiceError):
    code = "MODEL_RATE_LIMIT_ERROR"


class ModelRequestError(ModelServiceError):
    code = "MODEL_REQUEST_ERROR"


def translate_openai_error(
    error: Exception,
    *,
    provider_name: str,
    base_url: str,
    max_retries: int,
) -> ModelServiceError:
    """把 SDK 异常转换成不泄露密钥、可操作的中文诊断信息。"""

    host = urlparse(base_url).hostname or base_url
    attempts = max_retries + 1

    if isinstance(error, APITimeoutError):
        return ModelTimeoutError(
            f"连接 {provider_name}（{host}）超时，已自动尝试 {attempts} 次。"
            "请检查网络、代理或防火墙后重新执行。"
        )
    if isinstance(error, APIConnectionError):
        return ModelConnectionError(
            f"无法连接 {provider_name}（{host}），已自动尝试 {attempts} 次。"
            "本机当前没有建立可用的模型服务连接；请检查网络以及代理软件的"
            "系统代理或 TUN 模式是否真正可用。"
        )
    if isinstance(error, (AuthenticationError, PermissionDeniedError)):
        return ModelAuthenticationError(
            f"{provider_name} 拒绝了身份验证。请检查 API Key 是否正确、有效并具有模型访问权限。"
        )
    if isinstance(error, RateLimitError):
        return ModelRateLimitError(
            f"{provider_name} 当前触发了请求频率或账户额度限制。"
            "请稍后重试，并检查账户余额与并发限制。"
        )
    if isinstance(error, APIStatusError):
        request_id = getattr(error, "request_id", None)
        request_suffix = f"，请求编号 {request_id}" if request_id else ""
        return ModelRequestError(
            f"{provider_name} 返回 HTTP {error.status_code}{request_suffix}。"
            "请检查模型名称、请求参数以及服务状态。"
        )
    return ModelRequestError(f"{provider_name} 调用失败：{type(error).__name__}。")
