"""Советы по адаптации резюме под вакансию (US-05).

Вакансия берётся через hh_client.get_vacancy() (AC 5.1), советы генерирует LLM из
llm_providers.cv_processing (AC 1.5, AC 5.2), т.к. в запрос передаётся резюме.
Промпт — prompts/cv_tips.md; загрузка промпта, язык и текст запроса — общие с письмом (src/cover_letter.py).
"""

from typing import Any

from src.config import PROJECT_ROOT
from src.cover_letter import LANGUAGES, load_prompt

PROMPT_PATH = PROJECT_ROOT / "prompts" / "cv_tips.md"  # текст инструкции для LLM
SECTIONS = ("Опыт", "Навыки", "О себе")  # разделы резюме, в которые предлагаются правки (AC 5.2)

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
