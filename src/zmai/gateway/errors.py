"""Backend HTTP 错误映射 — 将 API 原始错误转换为用户可读消息。

所有 Backend 的 HTTPError 处理必须使用 ``friendly_http_error()``。
禁止将原始 API 响应体（含 request_id、HTTP 状态码、原始错误 JSON）传递到上层。
"""

from __future__ import annotations


def friendly_http_error(
    status: int,
    backend_name: str,
    model: str = "",
    env_key: str = "",
    raw_body: str = "",
) -> str:
    """将 HTTP 状态码映射为友好错误消息。

    Args:
        status: HTTP 状态码。
        backend_name: Backend 名称（如 "deepseek"）。
        model: 模型名称。
        env_key: 环境变量名（如 "DEEPSEEK_API_KEY"）。
        raw_body: 原始响应体（仅用于提取错误分类，不输出到用户）。

    Returns:
        用户可读的错误消息，含错误码前缀。
        绝不包含原始 API 响应体、request_id、完整 API Key。
    """
    if not env_key:
        env_key = f"{backend_name.upper()}_API_KEY"

    # 401 — Unauthorized
    if status == 401:
        return (
            f"[KEY_INVALID] {backend_name}: API Key 无效。\n"
            f"请执行 `zmai auth update {backend_name}` 更新 Key，\n"
            f"或者设置环境变量 {env_key}。"
        )

    # 403 — Forbidden (可能过期或权限不足)
    if status == 403:
        body_lower = raw_body.lower()
        if any(kw in body_lower for kw in ("expired", "disabled", "deactivated")):
            return (
                f"[KEY_EXPIRED] {backend_name}: API Key 已过期或已被禁用。\n"
                f"请在 {backend_name} 官网重新生成 Key。"
            )
        return (
            f"[KEY_INVALID] {backend_name}: API Key 权限不足 (HTTP 403)。\n"
            f"请检查 Key 是否有模型访问权限。"
        )

    # 404 — Not Found (模型不存在)
    if status == 404:
        return (
            f"[MODEL_NOT_FOUND] {backend_name}: 模型 '{model}' 不存在。\n"
            f"请检查 model 配置。"
        )

    # 429 — Rate Limited
    if status == 429:
        return (
            f"[RATE_LIMITED] {backend_name}: 请求过于频繁 (HTTP 429)。\n"
            f"请稍后重试，或降低请求频率。"
        )

    # 400 — Bad Request
    if status == 400:
        return (
            f"[BAD_REQUEST] {backend_name}: 请求参数错误 (HTTP 400)。\n"
            f"请检查模型名称和请求参数。"
        )

    # 5xx — Server Error
    if 500 <= status < 600:
        return (
            f"[SERVER_ERROR] {backend_name}: 服务暂时不可用 (HTTP {status})。\n"
            f"请稍后重试。"
        )

    # 其他 4xx
    if 400 <= status < 500:
        return (
            f"[BACKEND_ERROR] {backend_name}: 请求被拒绝 (HTTP {status})。\n"
            f"请稍后重试。"
        )

    # 未知
    return (
        f"[BACKEND_ERROR] {backend_name}: 返回错误 (HTTP {status})。\n"
        f"请稍后重试。"
    )


def validate_backend_response(
    data: dict,
    provider: str,
    *,
    required_fields: list[str] | None = None,
) -> None:
    """验证 Backend API 响应的基本结构。

    所有 Backend 的 invoke() / _parse_response() 必须在解析前调用此函数。
    覆盖三大类 Invalid Response：

      1. {}                     — 空响应（API 返回了 200 但 body 是 {}）
      2. {"error": ...}         — API 层错误（非 HTTP 错误，在 body 内）
      3. 缺少必要字段            — 按 required_fields 逐一检查

    Args:
        data:          API 返回的 JSON dict。
        provider:      Backend 名称（用于错误消息）。
        required_fields: 期望存在的顶层字段列表。

    Raises:
        BackendInvalidResponse: 响应结构无效时抛出。
    """
    from zmai.errors import BackendInvalidResponse

    # 1. 空响应
    if not data:
        raise BackendInvalidResponse(
            "API 返回了空响应体", provider=provider,
        )

    # 2. API 层错误（非 HTTP 错误码，在 body 内）
    if "error" in data:
        err = data["error"]
        if isinstance(err, dict):
            msg = err.get("message", err.get("code", str(err)))
        else:
            msg = str(err)
        raise BackendInvalidResponse(
            f"API 返回错误: {msg}", provider=provider,
        )

    # 3. 缺少必要字段
    if required_fields:
        for field in required_fields:
            if field not in data:
                raise BackendInvalidResponse(
                    f"响应缺少必要字段 '{field}'", provider=provider,
                )
