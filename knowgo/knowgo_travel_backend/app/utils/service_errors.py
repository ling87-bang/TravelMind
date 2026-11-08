"""把服务商异常映射为稳定的公开错误，不暴露 SDK 堆栈/参数。"""
from openai import APIConnectionError, APITimeoutError, AuthenticationError, RateLimitError


def public_service_error(exc: Exception) -> dict:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, RateLimitError):
            return {"type": "model_rate_limit", "message": "模型服务当前受到限流或配额限制，本轮未完成。请稍后重试。"}
        if isinstance(current, (APITimeoutError, TimeoutError)):
            return {"type": "service_timeout", "message": "服务响应超时，本轮未完成。请稍后重试。"}
        if isinstance(current, AuthenticationError):
            return {"type": "model_auth_error", "message": "模型服务鉴权失败，请检查服务配置后重试。"}
        if isinstance(current, APIConnectionError):
            return {"type": "service_unavailable", "message": "暂时无法连接模型服务，请稍后重试。"}
        current = current.__cause__ or current.__context__
    return {"type": "service_error", "message": "本轮处理失败，已接收的内容仍保留。请稍后重试。"}
