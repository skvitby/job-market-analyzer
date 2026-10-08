"""Генерация сопроводительного письма под вакансию (US-04).

Вакансия берётся через hh_client.get_vacancy() (AC 4.1), письмо генерирует LLM из
llm_providers.cv_processing (AC 1.5, AC 4.2), т.к. в запрос передаётся резюме.
Каждая генерация — новый файл: cl_{id}.md, затем cl_{id}_v2.md, _v3.md … (AC 4.4).
"""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from src.analyzer import build_matchers, dictionary_cv_status, match_skills, normalize_dictionary
from src.config import PROJECT_ROOT, load_preferences
from src.file_utils import write_text_atomic
from src.hh_client import get_vacancy
from src.llm_service import ask_json, get_settings
from src.text_utils import count, normalize_text, quote_found

logger = logging.getLogger(__name__)

CV_PATH = PROJECT_ROOT / "profile" / "my_cv.md"
LETTERS_DIR = PROJECT_ROOT / "reports" / "cover_letters"
PROMPT_PATH = PROJECT_ROOT / "prompts" / "cover_letter.md"  # текст инструкции для LLM
# Пункты ручной проверки письма (AC 4.4); то, что проверяет код (цитаты, AC 4.6), сюда не входит.
BEFORE_SENDING = (
    "Все утверждения подтверждаются резюме (см. «На чём построено письмо»)",
    "Сроки и числа не завышены",
    "Язык и тон подходят вакансии",
    "Фразы читаются естественно",
)
MAX_WORDS_TOLERANCE = 1.2  # превышение ориентира объёма больше чем на 20% — предупреждение (AC 4.5)

LANGUAGES = {"ru": "русском", "en": "английском"}   # код -> форма для промпта («на … языке»)
LANGUAGE_NAMES = {"ru": "русский", "en": "английский"}
_LATIN_RE = re.compile(r"[A-Za-z]")
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
# Известные значения tone -> описание для модели; другие значения передаются как есть.
TONES = {
    "professional": "профессиональный, сдержанный, без канцелярита",
    "professional_and_enthusiastic": "профессиональный, с живой заинтересованностью в задачах, но без восторженных штампов",
    "friendly": "дружелюбный и простой, но деловой",
    "formal": "официально-деловой",
}

# Порядок полей важен: модель сначала выписывает пары соответствия и неподтверждённые требования,
# а потом пишет письмо на их основе (prompts/cover_letter.md, AC 4.6).
LETTER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "matches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "requirement": {"type": "string"},
                    "cv_evidence": {"type": "string"},
                },
                "required": ["requirement", "cv_evidence"],
                "additionalProperties": False,
            },
        },
        "unconfirmed": {"type": "array", "items": {"type": "string"}},
        "letter": {"type": "string"},
    },
    "required": ["matches", "unconfirmed", "letter"],
    "additionalProperties": False,
}

WORD = ("слово", "слова", "слов")
_WORD_RE = re.compile(r"\w+(?:[-']\w+)*")
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
_FRONTMATTER_RE = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n", re.DOTALL)


def load_prompt(values: dict[str, Any], path: Optional[Path] = None) -> str:
    """Текст промпта из файла с подстановкой {плейсхолдеров}; frontmatter (описание для Obsidian) отбрасывается.

    path по умолчанию — PROMPT_PATH (читается при вызове, чтобы скрипт сравнения мог подставить вариант промпта).
    Все плейсхолдеры файла должны быть в values, и наоборот — иначе ValueError:
    так опечатка в файле промпта не уйдёт в модель незамеченной.
    """
    path = path or PROMPT_PATH
    shown = path.relative_to(PROJECT_ROOT) if path.is_relative_to(PROJECT_ROOT) else path
    if not path.exists():
        raise FileNotFoundError(f"Не найден файл промпта {shown}")
    text = _FRONTMATTER_RE.sub("", path.read_text(encoding="utf-8"), count=1)
    found = set(_PLACEHOLDER_RE.findall(text))
    if found != set(values):
        problems = []
        if found - set(values):
            problems.append("неизвестные: " + ", ".join(f"{{{k}}}" for k in sorted(found - set(values))))
        if set(values) - found:
            problems.append("нет в файле: " + ", ".join(f"{{{k}}}" for k in sorted(set(values) - found)))
        raise ValueError(f"Плейсхолдеры в {shown} не совпадают с ожидаемыми — "
                         + "; ".join(problems))
    return _PLACEHOLDER_RE.sub(lambda m: str(values[m.group(1)]), text).strip()


def detect_language(text: str) -> tuple[str, int, int]:
    """Язык текста для cover_letter_language = auto (AC 4.5): en, если латинских букв больше, чем кириллических.

    При равенстве — ru. Возвращает код языка и число латинских и кириллических букв (для лога).
    """
    latin = len(_LATIN_RE.findall(text))
    cyrillic = len(_CYRILLIC_RE.findall(text))
    return ("en" if latin > cyrillic else "ru"), latin, cyrillic


def resolve_language(prefs: dict[str, Any], vacancy: dict[str, Any]) -> str:
    """Код языка письма из cover_letter_language: ru, en или auto — по описанию вакансии (AC 4.5)."""
    setting = str(prefs.get("cover_letter_language") or "ru").strip().lower()
    if setting == "auto":
        language, latin, cyrillic = detect_language("\n".join(vacancy.get("description") or []))
        logger.info("Язык письма: %s (auto: латинских букв %d, кириллических %d)",
                    LANGUAGE_NAMES[language], latin, cyrillic)
        return language
    if setting not in LANGUAGES:
        raise ValueError(f"llm_preferences.cover_letter_language: неизвестное значение «{setting}». "
                         f"Допустимые: {', '.join(LANGUAGES)}, auto")
    return setting


def build_instruction(prefs: dict[str, Any], language: str) -> str:
    """Системная инструкция для LLM: prompts/cover_letter.md + значения из llm_preferences (AC 4.3, AC 4.5).

    language — код языка письма (ru / en), уже определённый resolve_language().
    """
    tone = str(prefs.get("tone") or "professional")
    focus = [str(area) for area in prefs.get("focus_areas") or []]
    return load_prompt({
        "language": LANGUAGES[language],
        "tone": TONES.get(tone, tone),
        "max_words": int(prefs.get("cover_letter_max_words") or 250),
        "focus_areas": "; ".join(focus) if focus else "не заданы",
    })


def build_input(vacancy: dict[str, Any], cv_text: str) -> str:
    """Текст запроса: вакансия (название, работодатель, описание, key_skills) и резюме."""
    parts = [
        "## Вакансия",
        f"Название: {vacancy.get('name') or '—'}",
        f"Работодатель: {vacancy.get('employer') or '—'}",
        f"Город: {vacancy.get('area') or '—'}",
    ]
    if vacancy.get("key_skills"):
        parts.append("Ключевые навыки: " + ", ".join(vacancy["key_skills"]))
    parts += ["", "### Описание", "\n".join(vacancy["description"]), "", "## Резюме", cv_text]
    return "\n".join(parts)


def count_words(text: str) -> int:
    """Количество слов в тексте письма."""
    return len(_WORD_RE.findall(text))


def next_version_path(vacancy_id: str, folder: Path, prefix: str = "cl") -> tuple[Path, int]:
    """Путь для новой версии файла: {prefix}_{id}.md, затем _v2, _v3 … (номер = наибольший + 1).

    Используется для писем (cl, AC 4.4) и советов по резюме (cv_tips, AC 5.5)."""
    version_re = re.compile(rf"^{re.escape(prefix)}_(\d+)(?:_v(\d+))?\.md$")
    versions = []
    for path in folder.glob(f"{prefix}_{vacancy_id}*.md"):
        match = version_re.match(path.name)
        if match and match.group(1) == vacancy_id:
            versions.append(int(match.group(2) or 1))
    version = max(versions, default=0) + 1
    stem = f"{prefix}_{vacancy_id}"
    name = f"{stem}.md" if version == 1 else f"{stem}_v{version}.md"
    return folder / name, version


def _yaml(value: Any) -> str:
    """Значение для frontmatter: строки в кавычках, чтобы двоеточия и # не ломали YAML."""
    if isinstance(value, (int, float)):
        return str(value)
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _cell(text: str) -> str:
    """Текст для ячейки Markdown-таблицы: без переносов строк и вертикальных черт."""
    return text.replace("|", "/").replace("\n", " ")


def render(vacancy: dict[str, Any], letter: str, matches: list[dict[str, Any]], unconfirmed: list[dict[str, Any]],
           language: str, version: int, model: str, words: int) -> str:
    """Markdown-файл письма для Obsidian (AC 4.4), блоки сверху вниз: метаданные, текст письма,
    требования без подтверждения (если есть), чек-лист «Перед отправкой», таблица пар соответствия.
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
        "words": words,
    }
    lines = ["---", *(f"{key}: {_yaml(value)}" for key, value in meta.items()), "tags: [cover-letter]", "---", ""]
    title = vacancy.get("name") or vacancy["id"]
    employer = f" — {vacancy['employer']}" if vacancy.get("employer") else ""
    lines += [f"# Сопроводительное письмо: {title}{employer}", ""]
    if meta["url"]:
        lines += [f"Вакансия: {meta['url']} · версия {version} · {count(words, *WORD)}", ""]
    lines += [letter.strip(), ""]

    if unconfirmed:
        lines += ["> [!warning] Требования вакансии без подтверждения в резюме",
                  "> В письме не упоминаются. Если опыт на самом деле есть — стоит добавить его в резюме;"
                  " если нет — подготовиться к вопросу на собеседовании.",
                  ">"]
        for item in unconfirmed:
            line = f"> - {_cell(item['text'])}"
            if item["in_cv"]:
                found = "; ".join(f"{skill} — «{_cell(fragment)}»" for skill, fragment in item["in_cv"])
                line += f" — ⚠️ возможно, есть в резюме: {found}"
            lines.append(line)
        if any(item["in_cv"] for item in unconfirmed):
            lines += [">", "> ⚠️ — навык из пункта найден в резюме: модель могла ошибиться, проверьте пункт."]
        lines.append("")

    lines += ["> [!todo] Перед отправкой", *(f"> - [ ] {item}" for item in BEFORE_SENDING), ""]

    lines.append("> [!info]- На чём построено письмо")
    if matches:
        lines += ["> | | Требование вакансии | Подтверждение в резюме |", "> |---|---|---|"]
        for item in matches:
            mark = "✅" if item.get("verified") else "⚠️"
            lines.append(f"> | {mark} | {_cell(item.get('requirement', ''))} | {_cell(item.get('cv_evidence', ''))} |")
        if not all(item.get("verified") for item in matches):
            lines += [">", "> ⚠️ — цитата не найдена в резюме: утверждения письма по этому пункту нужно проверить."]
    else:
        lines.append("> Прямых подтверждений требований вакансии в резюме не найдено.")
    lines.append("")
    return "\n".join(lines)


def mark_unconfirmed(items: list[str], cv_text: str,
                     dictionary: dict[str, list[str]]) -> list[dict[str, Any]]:
    """Требования без подтверждения с отметкой «возможно, есть в резюме» (AC 4.6, дефект D-4).

    Модель иногда относит к неподтверждённым то, что в резюме есть (Agile, Jira, SQL). Навыки словаря
    (AC 1.2), упомянутые в пункте, ищутся в резюме точным поиском с синонимами, как в AC 3.3;
    найденные возвращаются в in_cv парами (навык, фрагмент резюме). Навыки вне словаря не проверяются.
    """
    matchers = build_matchers(dictionary)
    marked = []
    for text in items:
        in_cv = []
        for skill in sorted(match_skills(text, matchers)):
            status, fragment = dictionary_cv_status(skill, dictionary, cv_text)
            if status != "missing":
                in_cv.append((skill, fragment.lstrip("- ")))
        marked.append({"text": text, "in_cv": in_cv})
    return marked


def check_answer(answer: dict[str, Any], cv_text: str, dictionary: Optional[dict[str, list[str]]] = None
                 ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Проверка достоверности ответа LLM (AC 4.6): цитаты сверяются с резюме так же, как в AC 3.3,
    требования без подтверждения — по словарю навыков.

    Возвращает пары соответствия с отметкой verified, требования без подтверждения с отметкой in_cv
    и предупреждения для терминала. Письмо сохраняется в любом случае — решение по отметкам
    принимает пользователь.
    """
    cv_norm = normalize_text(cv_text)
    matches = [{"requirement": str(m.get("requirement", "")).strip(),
                "cv_evidence": str(m.get("cv_evidence", "")).strip(),
                "verified": quote_found(str(m.get("cv_evidence", "")), cv_norm)}
               for m in answer.get("matches") or []]
    items = [str(item).strip() for item in answer.get("unconfirmed") or [] if str(item).strip()]
    unconfirmed = mark_unconfirmed(items, cv_text, dictionary or {})

    warnings = []
    if not matches:
        warnings.append("Резюме не подтверждает ни одного требования вакансии — проверьте, стоит ли откликаться")
    not_found = sum(not m["verified"] for m in matches)
    if not_found:
        warnings.append(f"Цитаты не найдены в резюме: {not_found} из {len(matches)} — "
                        "пары отмечены ⚠️ в файле письма, проверьте утверждения по ним")
    if unconfirmed:
        maybe = sum(bool(item["in_cv"]) for item in unconfirmed)
        note = f" (из них {maybe}, возможно, есть в резюме)" if maybe else ""
        warnings.append(f"Требований вакансии без подтверждения в резюме: {len(unconfirmed)}{note} — см. файл письма")
    return matches, unconfirmed, warnings


def generate_cover_letter(vacancy_id: str, letters_dir: Optional[Path] = None) -> tuple[Path, int, list[str]]:
    """Генерирует письмо по ID вакансии и сохраняет новую версию (US-04).

    letters_dir — папка для писем; по умолчанию reports/cover_letters/ (другая — для сравнения моделей,
    scripts/eval_cover_letters.py). Возвращает путь к файлу, номер версии и предупреждения.
    Ошибки: FileNotFoundError (нет резюме), HHApiError (вакансия), LLMError (NFR-5).
    """
    if not CV_PATH.exists():
        raise FileNotFoundError(f"Не найдено резюме {CV_PATH.relative_to(PROJECT_ROOT)} — оно нужно для письма")
    cv_text = CV_PATH.read_text(encoding="utf-8")
    vacancy = get_vacancy(vacancy_id)
    prefs = load_preferences()["llm_preferences"]
    settings = get_settings("cv_processing")
    language = resolve_language(prefs, vacancy)

    logger.info("Письмо: «%s» (%s), LLM %s/%s", vacancy.get("name"), vacancy.get("employer"),
                settings.provider, settings.model)
    answer = ask_json("cv_processing", build_instruction(prefs, language), build_input(vacancy, cv_text),
                      LETTER_SCHEMA)
    letter = answer["letter"].strip()
    if not letter:
        raise ValueError(f"{settings.model} вернул пустое письмо — попробуйте ещё раз")

    dictionary = normalize_dictionary(load_preferences()["analytical_skills_dictionary"])
    matches, unconfirmed, warnings = check_answer(answer, cv_text, dictionary)
    words = count_words(letter)
    max_words = int(prefs.get("cover_letter_max_words") or 250)
    if words > max_words * MAX_WORDS_TOLERANCE:
        warnings.append(f"Письмо длиннее ориентира: {count(words, *WORD)} при cover_letter_max_words = {max_words}")

    letters_dir = letters_dir or LETTERS_DIR
    letters_dir.mkdir(parents=True, exist_ok=True)
    path, version = next_version_path(str(vacancy["id"]), letters_dir)
    write_text_atomic(path, render(vacancy, letter, matches, unconfirmed, language, version, settings.model, words))
    shown = path.relative_to(PROJECT_ROOT) if path.is_relative_to(PROJECT_ROOT) else path
    logger.info("Письмо сохранено: %s (версия %d, %s)", shown, version, count(words, *WORD))
    return path, version, warnings
