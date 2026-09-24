"""Загрузка конфигурационного профиля profile/preferences.json (US-01)."""

import copy
import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PREFERENCES_PATH = PROJECT_ROOT / "profile" / "preferences.json"

# Значения по умолчанию из AC 2.1, AC 2.2, AC 3.1, AC 4.5, NFR-1 (используются, если файла или поля нет).
DEFAULT_PREFERENCES: dict[str, Any] = {
    "search_settings": {
        "target_roles": ["Бизнес-аналитик", "Системный аналитик", "Business Analyst", "Systems Analyst"],
        "regions": [{"id": 113, "name": "Россия"}, {"id": 16, "name": "Беларусь"}],
        "experience_level": None,
        "employment_type": None,
        "schedule": None,
        "currency": None,
        "min_salary": None,
        "exclude_words": [],
        "initial_period_days": 14,
        "request_delay_sec": 3.0,
    },
    "analytical_skills_dictionary": [
        "SQL", "UML", "BPMN", "REST API", "JSON",
        "Swagger", "Postman", "Python", "Agile", "Scrum",
    ],
    # AC 1.2: навыки, которые не показываются в отчёте (не целевые для пользователя).
    "excluded_skills": [],
    "llm_preferences": {
        "cover_letter_language": "ru",
        "tone": "professional",
        "focus_areas": [],
        # AC 1.5, ADR-001: провайдер и модель LLM отдельно для вакансий и для резюме.
        "llm_providers": {
            "vacancy_analysis": {"provider": "anthropic", "model": "claude-haiku-4-5"},
            "cv_processing": {"provider": "anthropic", "model": "claude-haiku-4-5"},
        },
    },
}


def _merge(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Рекурсивно накладывает значения пользователя на значения по умолчанию."""
    result = copy.deepcopy(defaults)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_preferences(path: Optional[Path] = None) -> dict[str, Any]:
    """Возвращает настройки: значения из файла поверх значений по умолчанию (AC 1.4).

    Если файла нет, используются только значения по умолчанию. Если файл
    содержит некорректный JSON, выбрасывается ValueError с понятным сообщением.
    """
    path = path or PREFERENCES_PATH
    if not path.exists():
        logger.warning("Файл %s не найден, используются значения по умолчанию", path)
        return copy.deepcopy(DEFAULT_PREFERENCES)

    try:
        user_prefs = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.error("Некорректный JSON в %s: %s", path, exc)
        raise ValueError(f"Некорректный JSON в файле {path}: {exc}") from exc

    if not isinstance(user_prefs, dict):
        raise ValueError(f"Файл {path} должен содержать JSON-объект верхнего уровня")

    return _merge(DEFAULT_PREFERENCES, user_prefs)
