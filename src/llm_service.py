"""Интеграция с LLM: Claude API и OpenAI-совместимые провайдеры (AC 1.5, ADR-001).

Провайдер и модель задаются отдельно для каждой задачи в llm_preferences.llm_providers
(profile/preferences.json): vacancy_analysis — вакансии, cv_processing — всё, где есть резюме.
Остальной код вызывает только ask_json() и не знает, какой провайдер используется.
"""

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

import anthropic
import openai
from dotenv import load_dotenv

from src.config import PROJECT_ROOT, load_preferences

logger = logging.getLogger(__name__)

TASKS = ("vacancy_analysis", "cv_processing")
MAX_RETRIES = 3            # повторы при 429/5xx и сетевых сбоях (NFR-5), паузы — экспоненциальные, средствами SDK
MAX_OUTPUT_TOKENS = 8192   # запас на ответ по пакету вакансий

# Параметры провайдеров по умолчанию (ADR-001). base_url и batch_size можно переопределить в preferences.json.
PROVIDERS: dict[str, dict[str, Any]] = {
    "anthropic": {"client": "anthropic", "key_env": "ANTHROPIC_API_KEY", "base_url": None,
                  "batch_size": 10, "timeout": 120, "console": "Anthropic Console (console.anthropic.com)"},
    "deepseek": {"client": "openai", "key_env": "DEEPSEEK_API_KEY", "base_url": "https://api.deepseek.com",
                 "batch_size": 10, "timeout": 120, "console": "кабинете DeepSeek (platform.deepseek.com)"},
    "qwen": {"client": "openai", "key_env": "DASHSCOPE_API_KEY",
             "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
             "batch_size": 10, "timeout": 120, "console": "Alibaba Cloud Model Studio"},
    # Малые локальные модели при пакете из нескольких вакансий теряют часть из них (ADR-001, раздел 6),
    # поэтому отправляем по одной. Контекст Ollama по умолчанию — 4096 токенов.
    # Первый запрос загружает модель в память (до пары минут на слабой видеокарте), поэтому таймаут больше.
    "ollama": {"client": "openai", "key_env": None, "base_url": "http://localhost:11434/v1",
               "batch_size": 1, "timeout": 600, "console": None},
}


class LLMError(RuntimeError):
    """Ошибка обращения к LLM с понятным пользователю описанием."""


@dataclass(frozen=True)
class LLMSettings:
    """Настройки LLM для одной задачи."""

    task: str
    provider: str
    model: str
    base_url: Optional[str]
    batch_size: int
    timeout: float


def get_settings(task: str) -> LLMSettings:
    """Настройки провайдера для задачи из llm_preferences.llm_providers (AC 1.5)."""
    if task not in TASKS:
        raise ValueError(f"Неизвестная задача LLM: {task}. Допустимые: {', '.join(TASKS)}")
    conf = load_preferences()["llm_preferences"]["llm_providers"].get(task) or {}
    provider = str(conf.get("provider", "")).lower()
    if provider not in PROVIDERS:
        raise LLMError(f"llm_providers.{task}: неизвестный provider «{provider}». "
                       f"Допустимые: {', '.join(PROVIDERS)}")
    if not conf.get("model"):
        raise LLMError(f"llm_providers.{task}: не задана model")
    defaults = PROVIDERS[provider]
    return LLMSettings(
        task=task,
        provider=provider,
        model=str(conf["model"]),
        base_url=conf.get("base_url") or defaults["base_url"],
        batch_size=int(conf.get("batch_size") or defaults["batch_size"]),
        timeout=float(defaults["timeout"]),
    )


def _api_key(settings: LLMSettings) -> str:
    """Ключ провайдера из .env (NFR-2). Для Ollama ключ не нужен."""
    key_env = PROVIDERS[settings.provider]["key_env"]
    if key_env is None:
        return "ollama"  # OpenAI-клиент требует непустой ключ, Ollama его не проверяет
    load_dotenv(PROJECT_ROOT / ".env")
    key = os.getenv(key_env)
    if not key:
        raise LLMError(f"В .env не задан {key_env} — ключ для провайдера {settings.provider}")
    return key


def _describe_error(settings: LLMSettings, exc: Exception) -> str:
    """Переводит ошибку SDK в понятное сообщение с подсказкой, что делать (NFR-5)."""
    where = f"{settings.provider} / {settings.model}"
    console = PROVIDERS[settings.provider]["console"]
    message = str(getattr(exc, "message", exc))
    status = getattr(exc, "status_code", None)

    if isinstance(exc, (anthropic.APIConnectionError, openai.APIConnectionError)):
        if settings.provider == "ollama":
            return f"Ollama не отвечает на {settings.base_url}: запустите приложение Ollama"
        return f"Нет соединения с {settings.provider} ({where}): проверьте интернет/VPN"
    if isinstance(exc, (anthropic.AuthenticationError, openai.AuthenticationError)):
        return f"Ключ {settings.provider} недействителен или истёк — создайте новый в {console} и обновите .env"
    if isinstance(exc, (anthropic.PermissionDeniedError, openai.PermissionDeniedError)):
        return f"Нет доступа к {where}: {message[:300]}"
    if isinstance(exc, (anthropic.NotFoundError, openai.NotFoundError)):
        if settings.provider == "ollama":
            return f"Модель {settings.model} не скачана в Ollama — выполните: ollama pull {settings.model}"
        return f"Модель {settings.model} не найдена у провайдера {settings.provider} — проверьте llm_providers.{settings.task}.model"
    if status == 402 or "credit balance" in message.lower() or "insufficient balance" in message.lower():
        return f"Закончился баланс {settings.provider} — пополните в {console}"
    if isinstance(exc, (anthropic.RateLimitError, openai.RateLimitError)):
        return f"Превышен лимит запросов {where} (повторы не помогли) — попробуйте позже"
    return f"Ошибка {where}: {message[:300]}"


def _ask_anthropic(settings: LLMSettings, instruction: str, text: str,
                   schema: dict[str, Any]) -> tuple[str, dict[str, int]]:
    """Запрос к Claude с ответом строго по JSON-схеме (structured outputs)."""
    client = anthropic.Anthropic(api_key=_api_key(settings), base_url=settings.base_url,
                                 max_retries=MAX_RETRIES, timeout=settings.timeout)
    response = client.messages.create(
        model=settings.model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=instruction,
        messages=[{"role": "user", "content": text}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    if response.stop_reason == "max_tokens":
        raise LLMError(f"Ответ {settings.model} обрезан по лимиту токенов — уменьшите batch_size")
    if response.stop_reason == "refusal":
        raise LLMError(f"{settings.model} отказался отвечать на запрос")
    usage = {"input": response.usage.input_tokens, "output": response.usage.output_tokens}
    return next(block.text for block in response.content if block.type == "text"), usage


def _ask_openai_compatible(settings: LLMSettings, instruction: str, text: str,
                           schema: dict[str, Any]) -> tuple[str, dict[str, int]]:
    """Запрос к OpenAI-совместимому провайдеру (DeepSeek, Qwen, Ollama) в JSON-режиме.

    JSON-режим гарантирует только корректный JSON, но не структуру, поэтому схема
    передаётся в инструкции, а ответ проверяется в _validate().
    """
    client = openai.OpenAI(api_key=_api_key(settings), base_url=settings.base_url,
                           max_retries=MAX_RETRIES, timeout=settings.timeout)
    system = f"{instruction}\n\nОтветь только JSON-объектом по схеме:\n{json.dumps(schema, ensure_ascii=False)}"
    response = client.chat.completions.create(
        model=settings.model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": text}],
        response_format={"type": "json_object"},
        max_tokens=MAX_OUTPUT_TOKENS,
    )
    choice = response.choices[0]
    if choice.finish_reason == "length":
        raise LLMError(f"Ответ {settings.model} обрезан по лимиту токенов — уменьшите batch_size")
    usage = {"input": response.usage.prompt_tokens, "output": response.usage.completion_tokens} \
        if response.usage else {"input": 0, "output": 0}
    return choice.message.content or "", usage


def _validate(data: Any, schema: dict[str, Any], settings: LLMSettings) -> None:
    """Проверяет верхний уровень ответа: объект и обязательные поля нужного типа."""
    types = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float), "boolean": bool}
    if not isinstance(data, dict):
        raise LLMError(f"{settings.model} вернул не JSON-объект")
    for field in schema.get("required", []):
        if field not in data:
            raise LLMError(f"В ответе {settings.model} нет обязательного поля «{field}»")
        expected = types.get(schema.get("properties", {}).get(field, {}).get("type", ""))
        if expected and not isinstance(data[field], expected):
            raise LLMError(f"В ответе {settings.model} поле «{field}» неверного типа")


def ask_json(task: str, instruction: str, text: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Отправляет текст в LLM, заданную для задачи, и возвращает ответ как словарь.

    task — vacancy_analysis или cv_processing (AC 1.5); instruction — системная инструкция;
    text — данные (вакансии, резюме); schema — JSON-схема ожидаемого ответа.
    Временные сбои повторяются до MAX_RETRIES раз, остальные ошибки -> LLMError (NFR-5).
    """
    settings = get_settings(task)
    ask = _ask_anthropic if PROVIDERS[settings.provider]["client"] == "anthropic" else _ask_openai_compatible
    try:
        raw, usage = ask(settings, instruction, text, schema)
    except (anthropic.APIError, openai.APIError) as exc:
        logger.error("LLM %s/%s: %s", settings.provider, settings.model, exc)
        raise LLMError(_describe_error(settings, exc)) from exc

    logger.info("LLM %s/%s: %d токенов на вход, %d на выход",
                settings.provider, settings.model, usage["input"], usage["output"])
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMError(f"{settings.model} вернул некорректный JSON: {raw[:200]}") from exc
    _validate(data, schema, settings)
    return data
