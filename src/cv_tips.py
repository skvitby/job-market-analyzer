"""Советы по адаптации резюме под вакансию (US-05).

Вакансия берётся через hh_client.get_vacancy() (AC 5.1), советы генерирует LLM из
llm_providers.cv_processing (AC 1.5, AC 5.2), т.к. в запрос передаётся резюме.
Промпт — prompts/cv_tips.md; загрузка промпта, язык и текст запроса — общие с письмом (src/cover_letter.py).
"""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from src.analyzer import build_matchers, dictionary_cv_status, match_skills, normalize_dictionary
from src.config import PROJECT_ROOT, load_preferences
from src.file_utils import write_text_atomic
from src.cover_letter import (CV_PATH, LANGUAGE_NAMES, LANGUAGES, _cell, _yaml, build_input, detect_language,
                              load_prompt, mark_unconfirmed, next_version_path)
from src.hh_client import get_vacancy
from src.llm_service import ask_json, get_settings
from src.text_utils import count, normalize_text, quote_found

logger = logging.getLogger(__name__)

PROMPT_PATH = PROJECT_ROOT / "prompts" / "cv_tips.md"  # текст инструкции для LLM
TIPS_DIR = PROJECT_ROOT / "reports" / "cv_tips"
# Пункты ручной проверки советов (AC 5.5); то, что проверяет код (AC 5.4), сюда не входит.
BEFORE_EDITING = (
    "«Стало» не завышает сроки, числа и результаты",
    "Термин действительно относится к моему опыту",
    "Каждую формулировку готов(а) подтвердить на интервью",
)
TIP = ("совет", "совета", "советов")
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


def terms_not_in_cv(text: str, cv_text: str, dictionary: dict[str, list[str]]) -> list[str]:
    """Навыки словаря из текста, которых нет нигде в резюме (AC 5.4, Q14).

    Ловит советы, где «стало» выходит за рамки основания и вписывает то, чего в резюме нет
    (REST API, Kanban). Точный поиск с синонимами, как в AC 3.3; навыки вне словаря не проверяются.
    """
    return sorted(skill for skill in match_skills(text, build_matchers(dictionary))
                  if dictionary_cv_status(skill, dictionary, cv_text)[0] == "missing")


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
        tip["terms_missing"] = terms_not_in_cv(tip["after"], cv_text, dictionary)
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
    bad_terms = sum(bool(tip["terms_missing"]) for tip in tips)
    if bad_terms:
        warnings.append(f"Термин не найден в резюме — советов: {bad_terms} из {len(tips)}, отмечены ⚠️ в файле")
    if gaps:
        maybe = sum(bool(item["in_cv"]) for item in gaps)
        note = f" (из них {maybe}, возможно, есть в резюме)" if maybe else ""
        warnings.append(f"Пробелов — требований вакансии без подтверждения в резюме: {len(gaps)}{note} — см. файл")
    return tips, gaps, warnings


def render(vacancy: dict[str, Any], tips: list[dict[str, Any]], gaps: list[dict[str, Any]],
           language: str, version: int, model: str) -> str:
    """Markdown-файл советов для Obsidian (AC 5.5), блоки сверху вниз: метаданные, советы с отметками
    проверок (AC 5.4), пробелы (если есть), чек-лист «Перед правкой резюме».

    «Стало» выводится блоком кода — в Obsidian его можно скопировать кнопкой.
    """
    meta = {
        "vacancy_id": vacancy["id"],
        "vacancy": vacancy.get("name") or "",
        "employer": vacancy.get("employer") or "",
        "area": vacancy.get("area") or "",
        "url": (vacancy.get("alternate_url") or "").split("?")[0],
        "version": version,
        "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "model": model,
        "language": language,
        "tips": len(tips),
    }
    lines = ["---", *(f"{key}: {_yaml(value)}" for key, value in meta.items()), "tags: [cv-tips]", "---", ""]
    title = vacancy.get("name") or vacancy["id"]
    employer = f" — {vacancy['employer']}" if vacancy.get("employer") else ""
    lines += [f"# Советы по резюме: {title}{employer}", ""]
    if meta["url"]:
        lines += [f"Вакансия: {meta['url']} · версия {version} · {count(len(tips), *TIP)}", ""]

    if not tips:
        lines += ["Советов нет: резюме уже использует термины вакансии или не подтверждает её требований"
                  + (" — см. пробелы ниже." if gaps else "."), ""]
    for number, tip in enumerate(tips, 1):
        lines += [f"## {number}. Раздел «{tip['section']}»", "",
                  f"- **Требование вакансии:** «{_cell(tip['vacancy_quote'])}»",
                  f"- **Основание в резюме:** «{_cell(tip['cv_quote'])}»"
                  + ("" if tip["verified"] else " — ⚠️ цитата не найдена в резюме, проверьте, есть ли такой опыт"),
                  f"- **Почему:** {_cell(tip['why'])}", "",
                  "**Стало:**", "```text", tip["after"], "```"]
        if tip["numbers_missing"]:
            lines.append(f"⚠️ Число не найдено в резюме: {', '.join(tip['numbers_missing'])} — проверьте формулировку.")
        if tip["terms_missing"]:
            lines.append(f"⚠️ Термин не найден в резюме: {', '.join(tip['terms_missing'])} — возможно, опыта нет;"
                         " такой совет лучше отбросить.")
        for skill, fragment in tip["in_section"]:
            lines.append(f"ℹ️ Термин уже есть в разделе: {skill} — «{_cell(fragment).rstrip('.')}».")
        lines.append("")

    if gaps:
        lines += ["> [!warning] Пробелы: требования вакансии без подтверждения в резюме",
                  "> Советов по ним нет. Если опыт на самом деле есть — стоит добавить его в резюме;"
                  " если нет — подготовиться к вопросу на собеседовании.",
                  ">"]
        for item in gaps:
            line = f"> - {_cell(item['text'])}"
            if item["in_cv"]:
                found = "; ".join(f"{skill} — «{_cell(fragment)}»" for skill, fragment in item["in_cv"])
                line += f" — ⚠️ возможно, есть в резюме: {found}"
            lines.append(line)
        if any(item["in_cv"] for item in gaps):
            lines += [">", "> ⚠️ — навык из пункта найден в резюме: модель могла ошибиться, проверьте пункт."]
        lines.append("")

    lines += ["> [!todo] Перед правкой резюме", *(f"> - [ ] {item}" for item in BEFORE_EDITING), ""]
    return "\n".join(lines)


def generate_cv_tips(vacancy_id: str, tips_dir: Optional[Path] = None) -> tuple[Path, int, list[str]]:
    """Советы по адаптации резюме под вакансию (US-05): LLM, проверки кодом и новая версия файла.

    tips_dir — папка для файлов; по умолчанию reports/cv_tips/. Повторный запуск не перезаписывает
    существующие файлы, а создаёт cv_tips_{id}_v2.md, _v3.md … (AC 5.5).
    Возвращает путь к файлу, номер версии и предупреждения.
    Ошибки: FileNotFoundError (нет резюме), HHApiError (вакансия), LLMError (NFR-5).
    """
    if not CV_PATH.exists():
        raise FileNotFoundError(f"Не найдено резюме {CV_PATH.relative_to(PROJECT_ROOT)} — оно нужно для советов")
    cv_text = CV_PATH.read_text(encoding="utf-8")
    vacancy = get_vacancy(vacancy_id)
    settings = get_settings("cv_processing")
    # Язык «стало» — по описанию вакансии (правило auto из AC 4.5), независимо от cover_letter_language.
    language, latin, cyrillic = detect_language("\n".join(vacancy.get("description") or []))
    logger.info("Советы: «%s» (%s), LLM %s/%s, язык «стало»: %s (латинских букв %d, кириллических %d)",
                vacancy.get("name"), vacancy.get("employer"), settings.provider, settings.model,
                LANGUAGE_NAMES[language], latin, cyrillic)
    answer = ask_json("cv_processing", build_instruction(language), build_input(vacancy, cv_text), TIPS_SCHEMA)
    dictionary = normalize_dictionary(load_preferences()["analytical_skills_dictionary"])
    tips, gaps, warnings = check_answer(answer, cv_text, dictionary)

    tips_dir = tips_dir or TIPS_DIR
    tips_dir.mkdir(parents=True, exist_ok=True)
    path, version = next_version_path(str(vacancy["id"]), tips_dir, "cv_tips")
    write_text_atomic(path, render(vacancy, tips, gaps, language, version, settings.model))
    shown = path.relative_to(PROJECT_ROOT) if path.is_relative_to(PROJECT_ROOT) else path
    logger.info("Советы сохранены: %s (версия %d, %s)", shown, version, count(len(tips), *TIP))
    return path, version, warnings
