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
        "professional_roles": [],
        "exclude_title_words": [],
        "relevance_check": None,
        "initial_period_days": 14,
        "request_delay_sec": 3.0,
    },
    "vacancy_lists": {"include": [], "exclude": []},
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
        "cover_letter_max_words": 250,  # AC 4.5: ориентир объёма письма
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


ROLE_MODES = ("role", "check", "title")


def professional_roles(settings: dict[str, Any]) -> list[dict[str, str]]:
    """Роли HH из professional_roles (AC 1.1): [{"id", "name", "mode"}]; пустой список — без фильтра.

    Элемент — объект {"id", "name", "mode"}, как в regions, или просто ID.
    mode: role — роли достаточно, check — название или проверка описания, title — только с названием
    (по умолчанию). ID возвращаются строками, как их отдаёт HH API.
    """
    roles = settings.get("professional_roles") or []
    if not isinstance(roles, list):
        roles = [roles]
    result = []
    for role in roles:
        item = role if isinstance(role, dict) else {"id": role}
        role_id = str(item.get("id", "")).strip()
        if not role_id.isdigit():
            raise ValueError(f"Некорректная роль в professional_roles: {role!r} — нужен числовой ID роли HH")
        mode = item.get("mode") or "title"
        if mode not in ROLE_MODES:
            raise ValueError(f"Некорректный mode у роли {role_id} в professional_roles: «{mode}» — "
                             f"допустимо {', '.join(ROLE_MODES)}")
        result.append({"id": role_id, "name": item.get("name") or role_id, "mode": mode})
    return result


def professional_role_ids(settings: dict[str, Any]) -> list[str]:
    """ID ролей HH из professional_roles строками (AC 1.1)."""
    return [role["id"] for role in professional_roles(settings)]


def vacancy_list(prefs: dict[str, Any], kind: str) -> dict[str, str]:
    """Ручной список vacancy_lists.include / .exclude (AC 1.1): {ID вакансии: пометка}."""
    items = (prefs.get("vacancy_lists") or {}).get(kind) or []
    result = {}
    for item in items:
        item = item if isinstance(item, dict) else {"id": item}
        vacancy_id = str(item.get("id", "")).strip()
        if not vacancy_id.isdigit():
            raise ValueError(f"Некорректный ID в vacancy_lists.{kind}: {item!r} — нужен числовой ID вакансии HH")
        result[vacancy_id] = item.get("note") or ""
    return result
