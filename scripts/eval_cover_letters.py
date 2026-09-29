"""Прогон сопроводительных писем на наборе вакансий для сравнения моделей (приёмка US-04).

Генерирует письма по приёмочному набору (docs/testing/test-report-us04.md) на указанной модели,
не меняя profile/preferences.json, и сохраняет их в отдельную папку reports/eval/<модель>-<дата>/,
чтобы не смешивать с настоящими письмами. В конце печатает сводку для протокола.
Содержание писем (TC-8…TC-11) проверяется вручную — по резюме и вакансии.

Запуск из корня проекта (каждый прогон обращается к LLM и стоит денег, ~$0,01–0,04 за письмо):
    .venv\\Scripts\\python.exe -m scripts.eval_cover_letters --model claude-haiku-4-5
    .venv\\Scripts\\python.exe -m scripts.eval_cover_letters --provider ollama --model qwen3-4b-8k
    .venv\\Scripts\\python.exe -m scripts.eval_cover_letters --model claude-sonnet-5-5 --vacancy 137493556
    .venv\\Scripts\\python.exe -m scripts.eval_cover_letters --model claude-haiku-4-5 \\
        --prompt prompts/experiments/cover_letter_a.md --tone professional
"""

import argparse
import logging
import re
import time
from datetime import datetime
from pathlib import Path

from src import cover_letter, llm_service
from src.config import PROJECT_ROOT

# Приёмочный набор US-04 (интервью по требованиям, Q12).
DEFAULT_VACANCIES = (
    "137493556",  # Itransition — англоязычная вакансия (auto)
    "136902181",  # Банк БелВЭБ — банковский домен, много требований без подтверждения
    "137587921",  # Лайфтех — хорошее совпадение с опытом
)


def summarize(path) -> dict:
    """Сводка по файлу письма: слова, найденные цитаты, требования без подтверждения."""
    text = path.read_text(encoding="utf-8")
    rows = re.findall(r"^> \| (✅|⚠️) \|", text, re.MULTILINE)
    warning = re.search(r"> \[!warning\].*?(?:\n\n|\Z)", text, re.DOTALL)
    items = [line for line in warning.group(0).splitlines() if line.startswith("> - ")] if warning else []
    return {
        "words": int(re.search(r"^words: (\d+)", text, re.MULTILINE).group(1)),
        "quotes": f"{rows.count('✅')}/{len(rows)}",
        "unconfirmed": len(items),
        "maybe_in_cv": sum("возможно, есть в резюме" in line for line in items),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Прогон писем US-04 на наборе вакансий для сравнения моделей")
    parser.add_argument("--provider", default="anthropic", choices=sorted(llm_service.PROVIDERS))
    parser.add_argument("--model", required=True, help="например claude-haiku-4-5, claude-sonnet-5-5, qwen3-4b-8k")
    parser.add_argument("--vacancy", action="append", help="ID вакансии (можно несколько); по умолчанию — набор приёмки")
    parser.add_argument("--prompt", type=Path, help="вариант промпта вместо prompts/cover_letter.md")
    parser.add_argument("--tone", help="tone вместо значения из preferences.json, например professional")
    parser.add_argument("--plan", action="store_true",
                        help="добавить в ответ поле plan (перед letter): на какую цитату опирается каждый абзац")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    for http_logger in ("httpx", "httpx2"):
        logging.getLogger(http_logger).setLevel(logging.WARNING)

    # Модель подменяется только в этом процессе: preferences.json не меняется.
    defaults = llm_service.PROVIDERS[args.provider]
    settings = llm_service.LLMSettings(task="cv_processing", provider=args.provider, model=args.model,
                                       base_url=defaults["base_url"], batch_size=1,
                                       timeout=float(defaults["timeout"]))
    llm_service.get_settings = cover_letter.get_settings = lambda task: settings
    label = args.model
    if args.prompt:
        cover_letter.PROMPT_PATH = args.prompt.resolve()
        label += f"-{args.prompt.stem}"
    if args.tone:
        load = cover_letter.load_preferences
        cover_letter.load_preferences = lambda: {**load(), "llm_preferences": {**load()["llm_preferences"],
                                                                              "tone": args.tone}}
        label += f"-{args.tone}"
    if args.plan:
        # Поле plan вставляется перед letter: модель сначала связывает абзацы с цитатами, потом пишет.
        schema = cover_letter.LETTER_SCHEMA
        props = {k: v for k, v in schema["properties"].items() if k != "letter"}
        props["plan"] = {"type": "array", "items": {"type": "string"}}
        props["letter"] = schema["properties"]["letter"]
        cover_letter.LETTER_SCHEMA = {**schema, "properties": props,
                                      "required": [*[r for r in schema["required"] if r != "letter"], "plan", "letter"]}
        label += "-plan"

    out_dir = PROJECT_ROOT / "reports" / "eval" / f"{label}-{datetime.now():%Y%m%d-%H%M}"
    results = []
    for vacancy_id in args.vacancy or DEFAULT_VACANCIES:
        started = time.monotonic()
        try:
            path, _, warnings = cover_letter.generate_cover_letter(vacancy_id, out_dir)
        except Exception as exc:  # в сводку попадает и ошибка: прогон остальных вакансий продолжается
            results.append((vacancy_id, None, f"ошибка: {exc}"))
            continue
        results.append((vacancy_id, {**summarize(path), "sec": round(time.monotonic() - started)}, path.name))
        for warning in warnings:
            print(f"    Внимание: {warning}")

    prompt = args.prompt or cover_letter.PROMPT_PATH.relative_to(PROJECT_ROOT)
    print(f"\nМодель: {args.provider}/{args.model} · промпт: {prompt} · tone: {args.tone or 'из preferences.json'}"
          f" · письма: {out_dir.relative_to(PROJECT_ROOT)}\n")
    print("| Вакансия | Файл | Слов | Цитаты найдены | Без подтверждения | Из них, возможно, в резюме | Сек |")
    print("|---|---|---|---|---|---|---|")
    for vacancy_id, summary, note in results:
        if summary is None:
            print(f"| {vacancy_id} | {note} | | | | | |")
        else:
            print(f"| {vacancy_id} | {note} | {summary['words']} | {summary['quotes']} | {summary['unconfirmed']} "
                  f"| {summary['maybe_in_cv']} | {summary['sec']} |")


if __name__ == "__main__":
    main()
