"""Safe OpenAI failure classification shared by chat and model controls."""

import re

import httpx


QUOTA_CODES = {
    "insufficient_quota", "credit_balance_exhausted", "organization_spend_limit_exceeded",
    "project_spend_limit_exceeded", "organization_usage_limit_exceeded",
    "billing_hard_limit_reached", "account_deactivated",
}
TRANSIENT_CODES = {
    "model_not_found", "server_is_overloaded", "server_error",
    "rate_limit_exceeded", "slow_down", "timeout", "network_error",
    "service_unavailable",
}
MODEL_PARAMETER_CODES = {"unsupported_parameter", "unsupported_value", "model_not_found"}


class AIProviderFailure(Exception):
    def __init__(
        self, code: str, admin_message: str, fallback_allowed: bool,
        status_code: int | None = None,
    ):
        self.code = code
        self.admin_message = admin_message
        self.fallback_allowed = fallback_allowed
        self.status_code = status_code
        super().__init__(admin_message)


ProviderFailure = AIProviderFailure


def _safe_code(value: object) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", value):
        return value.lower()
    return None


def from_http_response(response: httpx.Response) -> AIProviderFailure:
    status = response.status_code
    try:
        body = response.json()
        error = body.get("error", {}) if isinstance(body, dict) else {}
        provider_code = _safe_code(error.get("code")) or _safe_code(error.get("type")) if isinstance(error, dict) else None
    except (ValueError, TypeError):
        provider_code = None
    code = provider_code or f"http_{status}"
    if status in (401, 403) or code in QUOTA_CODES:
        fallback = False
    elif status == 429 or status == 404 or status == 408 or status >= 500:
        fallback = True
    elif status == 400 and code in MODEL_PARAMETER_CODES:
        fallback = True
    else:
        fallback = False
    return AIProviderFailure(code, f"OpenAI HTTP {status}; code={code}", fallback, status)


def from_event(event: dict) -> AIProviderFailure:
    error = event.get("error")
    if not isinstance(error, dict):
        response = event.get("response")
        error = response.get("error") if isinstance(response, dict) else None
    code = _safe_code(error.get("code")) or _safe_code(error.get("type")) if isinstance(error, dict) else None
    code = code or "response_failed"
    fallback = code in TRANSIENT_CODES
    return AIProviderFailure(code, f"OpenAI stream failed; code={code}", fallback)


def from_network(exc: BaseException) -> AIProviderFailure:
    code = "timeout" if isinstance(exc, (TimeoutError, httpx.TimeoutException)) else "network_error"
    return AIProviderFailure(code, f"OpenAI transport failed; code={code}", True)


def user_message(failure: AIProviderFailure, language: str | None) -> str:
    if failure.code == "timeout":
        return {
            "kk": "AI жауапты уақытында дайындап үлгермеді. Сұрауды қайталаңыз. Себет өзгерген жоқ.",
            "en": "The AI response timed out. Please retry. Your cart was not changed.",
        }.get(language, "ИИ не успел подготовить ответ за отведённое время. Повторите запрос. Корзина не изменена.")
    configuration = failure.code == "missing_api_key" or failure.code in QUOTA_CODES or failure.status_code in (401, 403)
    if language == "kk":
        return (
            "AI қызметі қазір қолжетімсіз. Кейінірек қайталап көріңіз. Каталогтан іздеу мен себет қолжетімді."
            if configuration else
            "Сыртқы AI қызметі уақытша қолжетімсіз. Кейінірек қайталап көріңіз. Каталогтан іздеу мен себет қолжетімді."
        )
    if language == "en":
        return (
            "The AI service is unavailable right now. Please try later. Catalog search and the cart are available."
            if configuration else
            "The external AI service is temporarily unavailable. Please try later. Catalog search and the cart are available."
        )
    return (
        "AI-сервис сейчас недоступен. Попробуйте позже. Поиск по каталогу и корзина доступны."
        if configuration else
        "Внешний AI-сервис временно недоступен. Попробуйте позже. Поиск по каталогу и корзина доступны."
    )
