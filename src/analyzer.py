"""Парсинг навыков и Gap-анализ (US-03).

Навыки ищутся по словарю analytical_skills_dictionary из profile/preferences.json
(AC 1.2, AC 3.1) в полном описании вакансии из кэша data/details/, а если его нет —
в сниппетах requirement / responsibility. Дополнительно учитываются key_skills из HH.
"""

import json
import logging
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from src.config import PROJECT_ROOT, load_preferences
from src.hh_client import DATA_DIR, DETAILS_DIR

logger = logging.getLogger(__name__)

REPORTS_DIR = PROJECT_ROOT / "reports"
REPORT_PATH = REPORTS_DIR / "market_skills_summary.md"
TOP_UNMATCHED = 20  # сколько key_skills вне словаря показывать в отчёте

_CYRILLIC_END_RE = re.compile(r"[а-яё]$", re.IGNORECASE)
_WORD_SPLIT_RE = re.compile(r"[\s\-]+")


def normalize_dictionary(raw: Any) -> dict[str, list[str]]:
    """Приводит словарь навыков к виду {навык: [синонимы]}.

    Поддерживаются оба формата: список навыков ["SQL", "UML"] и объект
    {"REST API": ["REST", "RESTful"], "SQL": []}.
    """
    if isinstance(raw, dict):
        return {str(skill): [str(a) for a in (aliases or [])] for skill, aliases in raw.items()}
    if isinstance(raw, list):
        return {str(skill): [] for skill in raw}
    raise ValueError("analytical_skills_dictionary должен быть списком или объектом {навык: [синонимы]}")


def _term_pattern(term: str) -> str:
    """Регулярное выражение для термина.

    Слова термина могут разделяться пробелом, дефисом или ничем ("REST API" = "REST-API").
    Русские слова ищутся по основе: после них допускаются любые русские буквы,
    чтобы находились падежные формы ("техническ задани" -> "техническое задание").
    Английские термины ищутся целым словом ("Git" не находится внутри "GitLab").
    """
    parts = []
    for word in _WORD_SPLIT_RE.split(term.strip()):
        suffix = "[а-яё]*" if _CYRILLIC_END_RE.search(word) else ""
        parts.append(re.escape(word) + suffix)
    return r"(?<!\w)" + r"[\s\-]*".join(parts) + r"(?!\w)"


def build_matchers(dictionary: dict[str, list[str]]) -> list[tuple[str, re.Pattern]]:
    """Готовит пары (навык, регулярное выражение по навыку и его синонимам)."""
    return [
        (skill, re.compile("|".join(_term_pattern(t) for t in [skill, *aliases]), re.IGNORECASE))
        for skill, aliases in dictionary.items()
    ]


def match_skills(text: str, matchers: list[tuple[str, re.Pattern]]) -> set[str]:
    """Навыки из словаря, упомянутые в тексте."""
    return {skill for skill, pattern in matchers if pattern.search(text)}


def _published(vacancy: dict[str, Any]) -> Optional[datetime]:
    """Дата публикации вакансии (формат HH: 2026-09-20T10:00:00+0300)."""
    try:
        return datetime.strptime(vacancy["published_at"], "%Y-%m-%dT%H:%M:%S%z")
    except (KeyError, TypeError, ValueError):
        return None


def load_vacancies(data_path: Optional[Path] = None) -> tuple[list[dict[str, Any]], list[Path]]:
    """Загружает вакансии (AC 3.4).

    По умолчанию объединяет все data/raw_vacancies_*.json с дедупликацией по id:
    при повторах остаётся запись из самого свежего файла. data_path — один конкретный файл.
    Возвращает вакансии и список прочитанных файлов.
    """
    files = [data_path] if data_path else sorted(DATA_DIR.glob("raw_vacancies_*.json"))
    if not files:
        raise FileNotFoundError("В data/ нет файлов raw_vacancies_*.json — сначала выполните fetch")
    vacancies: dict[str, dict[str, Any]] = {}
    for path in files:
        for vacancy in json.loads(Path(path).read_text(encoding="utf-8"))["vacancies"]:
            vacancies[str(vacancy["id"])] = vacancy  # файлы по возрастанию времени — свежая запись перезаписывает
    return list(vacancies.values()), files


def filter_vacancies(vacancies: list[dict[str, Any]], days: Optional[int] = None,
                     area: Optional[str] = None) -> list[dict[str, Any]]:
    """Оставляет вакансии за последние days дней (по published_at) и/или из региона area."""
    if days is not None:
        since = datetime.now().astimezone() - timedelta(days=days)
        vacancies = [v for v in vacancies if (p := _published(v)) and p >= since]
    if area is not None:
        vacancies = [v for v in vacancies if (v.get("area") or "").lower() == area.lower()]
    return vacancies


def load_detail(vacancy_id: str) -> Optional[dict[str, Any]]:
    """Полное описание вакансии из кэша data/details/{id}.json или None."""
    path = DETAILS_DIR / f"{vacancy_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("Повреждён файл кэша %s (%s), использую сниппет", path, exc)
        return None


def vacancy_text(vacancy: dict[str, Any], detail: Optional[dict[str, Any]]) -> tuple[str, bool]:
    """Текст вакансии для поиска навыков и признак, что это полное описание (AC 3.1)."""
    if detail and detail.get("description"):
        return "\n".join(detail["description"]), True
    snippet = [vacancy.get("requirement"), vacancy.get("responsibility")]
    return "\n".join(s for s in snippet if s), False


def _format_date(value: Optional[datetime]) -> str:
    return f"{value:%d.%m.%Y}" if value else "—"


def build_report(vacancies: list[dict[str, Any]], dictionary: dict[str, list[str]],
                 files: list[Path], days: Optional[int], area: Optional[str]) -> str:
    """Считает частоту навыков и формирует Markdown-отчёт (AC 3.2)."""
    matchers = build_matchers(dictionary)
    skill_counts: Counter[str] = Counter()
    unmatched: Counter[str] = Counter()
    unmatched_names: dict[str, str] = {}
    full_count = 0

    for vacancy in vacancies:
        detail = load_detail(str(vacancy["id"]))
        text, is_full = vacancy_text(vacancy, detail)
        full_count += is_full
        key_skills = (detail or {}).get("key_skills") or []

        skills = match_skills(text, matchers)
        for key_skill in key_skills:
            found = match_skills(key_skill, matchers)
            skills |= found
            if not found:
                key = key_skill.strip().lower()
                unmatched[key] += 1
                unmatched_names.setdefault(key, key_skill.strip())
        skill_counts.update(skills)  # навык учитывается не более одного раза на вакансию

    total = len(vacancies)
    dates = [d for v in vacancies if (d := _published(v))]
    filters = []
    if area:
        filters.append(f"регион «{area}»")
    if days:
        filters.append(f"последние {days} дн.")

    lines = [
        "# Востребованные навыки BA/SA на рынке",
        "",
        f"- **Сформирован:** {datetime.now():%d.%m.%Y %H:%M}",
        f"- **Источник:** {', '.join(Path(f).name for f in files)}",
        f"- **Фильтры:** {', '.join(filters) if filters else 'нет'}",
        f"- **Период публикации:** {_format_date(min(dates, default=None))} — {_format_date(max(dates, default=None))}",
        f"- **Вакансий:** {total} (по полному описанию — {full_count}, по сниппетам — {total - full_count})",
    ]
    if total and full_count < total:
        lines.append("")
        lines.append(f"> [!warning] {total - full_count} вакансий без полного описания: в сниппетах часть "
                     "навыков обрезана, поэтому частоты могут быть занижены. Догрузите описания командой `fetch`.")

    lines += ["", "## Топ навыков", "", "| # | Навык | Вакансий | Доля |", "|---|---|---|---|"]
    for rank, (skill, count) in enumerate(skill_counts.most_common(), start=1):
        lines.append(f"| {rank} | {skill} | {count} | {count / total:.0%} |")

    missing = [skill for skill in dictionary if skill not in skill_counts]
    if missing:
        lines += ["", "## Не встретились ни в одной вакансии", "", ", ".join(missing)]

    if unmatched:
        lines += ["", f"## key_skills HH вне словаря (топ-{TOP_UNMATCHED})", "",
                  "Кандидаты на пополнение `analytical_skills_dictionary` в `profile/preferences.json`.", "",
                  "| Навык | Вакансий |", "|---|---|"]
        for key, count in unmatched.most_common(TOP_UNMATCHED):
            lines.append(f"| {unmatched_names[key]} | {count} |")

    return "\n".join(lines) + "\n"


def analyze(data_path: Optional[Path] = None, days: Optional[int] = None,
            area: Optional[str] = None) -> tuple[Path, int]:
    """Формирует reports/market_skills_summary.md. Возвращает путь к отчёту и число вакансий."""
    dictionary = normalize_dictionary(load_preferences()["analytical_skills_dictionary"])
    vacancies, files = load_vacancies(data_path)
    vacancies = filter_vacancies(vacancies, days, area)
    if not vacancies:
        raise ValueError("После применения фильтров не осталось вакансий")
    logger.info("Анализирую %d вакансий, словарь — %d навыков", len(vacancies), len(dictionary))

    REPORTS_DIR.mkdir(exist_ok=True)
    REPORT_PATH.write_text(build_report(vacancies, dictionary, files, days, area), encoding="utf-8")
    logger.info("Отчёт сохранён в %s", REPORT_PATH)
    return REPORT_PATH, len(vacancies)
