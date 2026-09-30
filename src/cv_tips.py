"""Советы по адаптации резюме под вакансию (US-05).

Вакансия берётся через hh_client.get_vacancy() (AC 5.1), советы генерирует LLM из
llm_providers.cv_processing (AC 1.5, AC 5.2), т.к. в запрос передаётся резюме.
Промпт — prompts/cv_tips.md; загрузка промпта, язык и текст запроса — общие с письмом (src/cover_letter.py).
"""

import logging
import re
from typing import Any, Optional

from src.analyzer import build_matchers, normalize_dictionary
from src.config import PROJECT_ROOT, load_preferences
from src.cover_letter import (CV_PATH, LANGUAGE_NAMES, LANGUAGES, build_input, detect_language, load_prompt,
                              mark_unconfirmed)
from src.hh_client import get_vacancy
from src.llm_service import ask_json, get_settings
from src.text_utils import normalize_text, quote_found

logger = logging.getLogger(__name__)

PROMPT_PATH = PROJECT_ROOT / "prompts" / "cv_tips.md"  # текст инструкции для LLM
SECTIONS = ("Опыт", "Навыки", "О себе")  # разделы резюме, в которые предлагаются правки (AC 5.2)
# Заголовки разделов в my_cv.md, где проверяется «термин уже есть в разделе» (AC 5.4); «Опыт» не проверяется.
SECTION_HEADINGS = {"Навыки": "Навыки", "О себе": "О себе|Обо мне"}
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")

# Порядок полей важен: модель сначала выписывает пробелы, а потом даёт советы только по
# подтверждённому опыту (AC 5.3); в совете цитаты идут раньше формулировки «стало».
TIPS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "gaps": {"type": "array", "items": {"type": "string"}},
        "tips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "section": {"type": "string", "enum": list(SECTIONS)},
                    "vacancy_quote": {"type": "string"},
                    "cv_quote": {"type": "string"},
                    "after": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["section", "vacancy_quote", "cv_quote", "after", "why"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["gaps", "tips"],
    "additionalProperties": False,
}


def build_instruction(language: str) -> str:
    """Системная инструкция для LLM: prompts/cv_tips.md с языком формулировки «стало» (AC 5.3).

    language — код языка вакансии (ru / en), определённый detect_language() по описанию.
    """
    return load_prompt({"language": LANGUAGES[language]}, PROMPT_PATH)


def numbers_not_in_cv(text: str, cv_text: str) -> list[str]:
    """Числа из текста (последовательности цифр), которых нет в резюме (AC 5.4).

    Дробная часть сравнивается без учёта разделителя: «1,5» в совете = «1.5» в резюме.
    Числа, записанные словами, не проверяются.
    """
    missing = []
    for number in dict.fromkeys(_NUMBER_RE.findall(text)):
        pattern = r"(?<![\d.,])" + "[.,]".join(re.split(r"[.,]", number)) + r"(?!\d|[.,]\d)"
        if not re.search(pattern, cv_text):
            missing.append(number)
    return missing


def cv_section(cv_text: str, section: str) -> Optional[str]:
    """Текст раздела резюме, куда предлагается правка: от заголовка до следующего заголовка того же
    или более высокого уровня. Для «Опыт» и разделов, которых нет в резюме, — None."""
    title_re = SECTION_HEADINGS.get(section)
    if not title_re:
        return None
    lines = cv_text.splitlines()
    for i, line in enumerate(lines):
        match = re.match(rf"^(#+)\s*(?:{title_re})\s*$", line.strip(), re.IGNORECASE)
        if not match:
            continue
        level = len(match.group(1))
        end = next((j for j in range(i + 1, len(lines))
                    if (m := re.match(r"^(#+)\s", lines[j].strip())) and len(m.group(1)) <= level), len(lines))
        return "\n".join(lines[i + 1:end])
    return None


def terms_in_section(tip: dict[str, str], cv_text: str,
                     dictionary: dict[str, list[str]]) -> list[tuple[str, str]]:
    """Навыки словаря из «стало», которые уже есть в целевом разделе резюме: пары (навык, фрагмент).

    Проверяются только советы для «Навыки» и «О себе» (AC 5.4, Q13): совет для «Опыт» строится
    на опыте из резюме, и термин там ожидаемо уже есть. Точный поиск с синонимами, как в AC 3.3;
    навыки вне словаря не проверяются.
    """
    section_text = cv_section(cv_text, tip["section"])
    if not section_text:
        return []
    found = []
    for skill, pattern in build_matchers(dictionary):
        if not pattern.search(tip["after"]) or not (match := pattern.search(section_text)):
            continue
        line = section_text[section_text.rfind("\n", 0, match.start()) + 1:].split("\n", 1)[0]
        # Фрагмент — пункт между «;» (раздел «Навыки» — одна строка через «;») или предложение.
        fragment = next((part for part in re.split(r";|(?<=[.!?])\s", line) if match.group(0) in part), line)
        found.append((skill, fragment.strip().lstrip("- ")))
    return sorted(found)


def check_answer(answer: dict[str, Any], cv_text: str, dictionary: Optional[dict[str, list[str]]] = None
                 ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Проверки ответа LLM кодом (AC 5.4).

    Советы получают отметки: verified (основание найдено в резюме, как в AC 3.3), numbers_missing
    (числа из «стало», которых нет в резюме), in_section (навыки словаря из «стало», уже найденные
    в целевом разделе резюме).
    Пробелы сверяются со словарём, как в AC 4.6. Советы сохраняются в любом случае — решение
    по отметкам принимает пользователь. Возвращает советы, пробелы и предупреждения для терминала.
    """
    dictionary = dictionary or {}
    cv_norm = normalize_text(cv_text)
    tips = []
    for raw in answer.get("tips") or []:
        tip = {key: str(raw.get(key, "")).strip() for key in ("section", "vacancy_quote", "cv_quote", "after", "why")}
        tip["verified"] = quote_found(tip["cv_quote"], cv_norm)
        tip["numbers_missing"] = numbers_not_in_cv(tip["after"], cv_text)
        tip["in_section"] = terms_in_section(tip, cv_text, dictionary)
        tips.append(tip)
    items = [str(item).strip() for item in answer.get("gaps") or [] if str(item).strip()]
    gaps = mark_unconfirmed(items, cv_text, dictionary)

    warnings = []
    if not tips:
        warnings.append("Советов нет: резюме уже использует термины вакансии или не подтверждает её требований"
                        " — см. блок пробелов в файле")
    not_found = sum(not tip["verified"] for tip in tips)
    if not_found:
        warnings.append(f"Цитата не найдена в резюме — советов: {not_found} из {len(tips)}, отмечены ⚠️ в файле")
    bad_numbers = sum(bool(tip["numbers_missing"]) for tip in tips)
    if bad_numbers:
        warnings.append(f"Число не найдено в резюме — советов: {bad_numbers} из {len(tips)}, отмечены ⚠️ в файле")
    if gaps:
        maybe = sum(bool(item["in_cv"]) for item in gaps)
        note = f" (из них {maybe}, возможно, есть в резюме)" if maybe else ""
        warnings.append(f"Пробелов — требований вакансии без подтверждения в резюме: {len(gaps)}{note} — см. файл")
    return tips, gaps, warnings


def generate_tips(vacancy_id: str) -> tuple[dict[str, Any], str, str, list[dict[str, Any]], list[dict[str, Any]],
                                            list[str]]:
    """Запрашивает у LLM советы по вакансии и проверяет их (AC 5.1–5.4).

    Возвращает вакансию, язык «стало», модель, советы, пробелы и предупреждения.
    Ошибки: FileNotFoundError (нет резюме), HHApiError (вакансия), LLMError (NFR-5).
    """
    if not CV_PATH.exists():
        raise FileNotFoundError(f"Не найдено резюме {CV_PATH.relative_to(PROJECT_ROOT)} — оно нужно для советов")
    cv_text = CV_PATH.read_text(encoding="utf-8")
    vacancy = get_vacancy(vacancy_id)
    settings = get_settings("cv_processing")
    language, latin, cyrillic = detect_language("\n".join(vacancy.get("description") or []))
    logger.info("Советы: «%s» (%s), LLM %s/%s, язык «стало»: %s (латинских букв %d, кириллических %d)",
                vacancy.get("name"), vacancy.get("employer"), settings.provider, settings.model,
                LANGUAGE_NAMES[language], latin, cyrillic)
    answer = ask_json("cv_processing", build_instruction(language), build_input(vacancy, cv_text), TIPS_SCHEMA)
    dictionary = normalize_dictionary(load_preferences()["analytical_skills_dictionary"])
    tips, gaps, warnings = check_answer(answer, cv_text, dictionary)
    return vacancy, language, settings.model, tips, gaps, warnings
