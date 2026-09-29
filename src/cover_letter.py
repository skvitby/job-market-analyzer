"""Генерация сопроводительного письма под вакансию (US-04).

Вакансия берётся через hh_client.get_vacancy() (AC 4.1), письмо генерирует LLM из
llm_providers.cv_processing (AC 1.5, AC 4.2), т.к. в запрос передаётся резюме.
Каждая генерация — новый файл: cl_{id}.md, затем cl_{id}_v2.md, _v3.md … (AC 4.4).
"""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import PROJECT_ROOT, load_preferences
from src.hh_client import get_vacancy
from src.llm_service import ask_json, get_settings
from src.text_utils import count

logger = logging.getLogger(__name__)

CV_PATH = PROJECT_ROOT / "profile" / "my_cv.md"
LETTERS_DIR = PROJECT_ROOT / "reports" / "cover_letters"
MAX_WORDS_TOLERANCE = 1.2  # превышение ориентира объёма больше чем на 20% — предупреждение (AC 4.5)

LANGUAGES = {"ru": "русском", "en": "английском"}
# Известные значения tone -> описание для модели; другие значения передаются как есть.
TONES = {
    "professional": "профессиональный, сдержанный, без канцелярита",
    "professional_and_enthusiastic": "профессиональный, с живой заинтересованностью в задачах, но без восторженных штампов",
    "friendly": "дружелюбный и простой, но деловой",
    "formal": "официально-деловой",
}

LETTER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "letter": {"type": "string"},
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
    },
    "required": ["letter", "matches"],
    "additionalProperties": False,
}

WORD = ("слово", "слова", "слов")
_WORD_RE = re.compile(r"\w+(?:[-']\w+)*")
_VERSION_RE = re.compile(r"^cl_(\d+)(?:_v(\d+))?\.md$")


def build_instruction(prefs: dict[str, Any]) -> str:
    """Системная инструкция для LLM из llm_preferences (AC 4.3, AC 4.5)."""
    language = LANGUAGES.get(str(prefs.get("cover_letter_language", "ru")).lower(), prefs.get("cover_letter_language"))
    tone = str(prefs.get("tone") or "professional")
    tone_text = TONES.get(tone, tone)
    max_words = int(prefs.get("cover_letter_max_words") or 250)
    focus = [str(area) for area in prefs.get("focus_areas") or []]
    focus_text = (
        "Приоритетные направления опыта кандидата: " + "; ".join(focus) + ". Делай на них акцент, "
        "только если они относятся к задачам вакансии и подтверждаются резюме.\n"
    ) if focus else ""

    return (
        "Ты помогаешь бизнес/системному аналитику написать сопроводительное письмо к отклику на вакансию на hh.ru.\n"
        "На вход — описание вакансии и резюме кандидата.\n\n"
        "Структура письма:\n"
        "1. Короткое приветствие и одна фраза: на какую позицию отклик и чем эта позиция интересна кандидату.\n"
        "2. 2–3 ключевые задачи или требования из вакансии, и к каждой — конкретный опыт из резюме "
        "(что сделано, в каком домене, с каким результатом). Не пересказывай резюме целиком.\n"
        "3. Вежливое предложение обсудить детали или провести интервью.\n"
        "4. Подпись — имя кандидата из резюме.\n\n"
        "Правила:\n"
        f"- Письмо пишется на {language} языке. Тон — {tone_text}.\n"
        f"- Объём — не более {max_words} слов, 3–4 коротких абзаца.\n"
        "- Используй только факты из резюме. Не придумывай опыт, инструменты, цифры и достижения. "
        "Если требование вакансии резюме не подтверждает — не упоминай его.\n"
        "- Не используй заглушки вида [Имя], [Компания]. Название компании бери из вакансии; "
        "если имени контактного лица нет — нейтральное приветствие.\n"
        "- Обычный текст без Markdown: без заголовков, списков, жирного шрифта. Абзацы разделяй пустой строкой.\n"
        "- Избегай штампов: «команда профессионалов», «динамично развивающаяся компания», «стрессоустойчивость».\n"
        f"{focus_text}\n"
        "В поле matches перечисли пункты соответствия, на которых построено письмо: requirement — требование "
        "или задача из вакансии, cv_evidence — дословная короткая цитата из резюме, которая его подтверждает."
    )


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


def next_letter_path(vacancy_id: str, letters_dir: Path = LETTERS_DIR) -> tuple[Path, int]:
    """Путь для новой версии письма: cl_{id}.md, затем _v2, _v3 … (номер = наибольший + 1, AC 4.4)."""
    versions = []
    for path in letters_dir.glob(f"cl_{vacancy_id}*.md"):
        match = _VERSION_RE.match(path.name)
        if match and match.group(1) == vacancy_id:
            versions.append(int(match.group(2) or 1))
    version = max(versions, default=0) + 1
    name = f"cl_{vacancy_id}.md" if version == 1 else f"cl_{vacancy_id}_v{version}.md"
    return letters_dir / name, version


def _yaml(value: Any) -> str:
    """Значение для frontmatter: строки в кавычках, чтобы двоеточия и # не ломали YAML."""
    if isinstance(value, (int, float)):
        return str(value)
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def render(vacancy: dict[str, Any], letter: str, matches: list[dict[str, str]],
           version: int, model: str, words: int) -> str:
    """Markdown-файл письма: метаданные для Obsidian, текст письма, пункты соответствия (AC 4.4)."""
    meta = {
        "vacancy_id": vacancy["id"],
        "vacancy": vacancy.get("name") or "",
        "employer": vacancy.get("employer") or "",
        "area": vacancy.get("area") or "",
        "url": (vacancy.get("alternate_url") or "").split("?")[0],
        "version": version,
        "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "model": model,
        "words": words,
    }
    lines = ["---", *(f"{key}: {_yaml(value)}" for key, value in meta.items()), "tags: [cover-letter]", "---", ""]
    title = vacancy.get("name") or vacancy["id"]
    employer = f" — {vacancy['employer']}" if vacancy.get("employer") else ""
    lines += [f"# Сопроводительное письмо: {title}{employer}", ""]
    if meta["url"]:
        lines += [f"Вакансия: {meta['url']} · версия {version} · {count(words, *WORD)}", ""]
    lines += [letter.strip(), ""]
    if matches:
        lines += ["> [!info]- На чём построено письмо (проверьте факты перед отправкой)",
                  "> | Требование вакансии | Подтверждение в резюме |", "> |---|---|"]
        for item in matches:
            requirement = item.get("requirement", "").replace("|", "/").replace("\n", " ")
            evidence = item.get("cv_evidence", "").replace("|", "/").replace("\n", " ")
            lines.append(f"> | {requirement} | {evidence} |")
        lines.append("")
    return "\n".join(lines)


def generate_cover_letter(vacancy_id: str) -> tuple[Path, int, list[str]]:
    """Генерирует письмо по ID вакансии и сохраняет новую версию (US-04).

    Возвращает путь к файлу, номер версии и предупреждения.
    Ошибки: FileNotFoundError (нет резюме), HHApiError (вакансия), LLMError (NFR-5).
    """
    if not CV_PATH.exists():
        raise FileNotFoundError(f"Не найдено резюме {CV_PATH.relative_to(PROJECT_ROOT)} — оно нужно для письма")
    cv_text = CV_PATH.read_text(encoding="utf-8")
    vacancy = get_vacancy(vacancy_id)
    prefs = load_preferences()["llm_preferences"]
    settings = get_settings("cv_processing")

    logger.info("Письмо: «%s» (%s), LLM %s/%s", vacancy.get("name"), vacancy.get("employer"),
                settings.provider, settings.model)
    answer = ask_json("cv_processing", build_instruction(prefs), build_input(vacancy, cv_text), LETTER_SCHEMA)
    letter = answer["letter"].strip()
    if not letter:
        raise ValueError(f"{settings.model} вернул пустое письмо — попробуйте ещё раз")

    warnings = []
    words = count_words(letter)
    max_words = int(prefs.get("cover_letter_max_words") or 250)
    if words > max_words * MAX_WORDS_TOLERANCE:
        warnings.append(f"Письмо длиннее ориентира: {count(words, *WORD)} при cover_letter_max_words = {max_words}")

    LETTERS_DIR.mkdir(parents=True, exist_ok=True)
    path, version = next_letter_path(str(vacancy["id"]))
    matches = answer.get("matches") or []
    path.write_text(render(vacancy, letter, matches, version, settings.model, words), encoding="utf-8")
    logger.info("Письмо сохранено: %s (версия %d, %s)", path.relative_to(PROJECT_ROOT), version, count(words, *WORD))
    return path, version, warnings
