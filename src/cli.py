"""Интерфейс командной строки.

Запуск из корня проекта: python -m src.cli <команда> [опции]
"""

import json
import logging
from pathlib import Path
from typing import Any, Optional

import typer

from src.analyzer import analyze as run_analysis
from src.hh_client import HHApiError, fetch_details, fetch_vacancies
from src.llm_service import LLMError

app = typer.Typer(help="Job Market Analyzer: сбор и анализ вакансий BA/SA.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Job Market Analyzer: сбор и анализ вакансий BA/SA."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")


@app.command()
def fetch(
    role: Optional[list[str]] = typer.Option(None, "--role", help="Роль для поиска (можно несколько параметров: --role \"Бизнес-аналитик\" --role \"Системный аналитик\")"),
    region: Optional[list[int]] = typer.Option(None, "--region", help="ID региона HH: 113 — Россия, 16 — Беларусь (можно несколько параметров: --region 113 --region 16)"),
    experience: Optional[list[str]] = typer.Option(None, "--experience", help="noExperience / between1And3 / between3And6 / moreThan6 (можно несколько параметров: --experience between1And3 --experience between3And6)"),
    employment: Optional[list[str]] = typer.Option(None, "--employment", help="full / part / project (можно несколько параметров: --employment full --employment project)"),
    schedule: Optional[list[str]] = typer.Option(None, "--schedule", help="office / hybrid / remote (можно несколько параметров: --schedule remote --schedule hybrid)"),
    min_salary: Optional[int] = typer.Option(None, "--min-salary", help="Минимальная зарплата (валюта — currency из профиля)"),
    no_details: bool = typer.Option(False, "--no-details", help="Не загружать полные описания вакансий в data/details/"),
) -> None:
    """Выгружает вакансии с HH.ru в data/raw_vacancies_{timestamp}.json (US-02).

    Без опций используются search_settings из profile/preferences.json,
    переданные опции их переопределяют. Затем догружает полные описания
    вакансий, которых ещё нет в кэше data/details/ (AC 2.7). С --region описания
    загружаются только для вакансий этих регионов (включая их города), без --region — для всех.

    Несколько параметров одной опции задаются повтором опции, например:
    fetch --region 113 --region 16 --schedule remote --schedule hybrid
    Значения с пробелами — в кавычках: --role "Системный аналитик".
    """
    overrides: dict[str, Any] = {}
    if role:
        overrides["target_roles"] = role
    if region:
        overrides["regions"] = [{"id": region_id, "name": str(region_id)} for region_id in region]
    if experience:
        overrides["experience_level"] = experience
    if employment:
        overrides["employment_type"] = employment
    if schedule:
        overrides["schedule"] = schedule
    if min_salary is not None:
        overrides["min_salary"] = min_salary

    try:
        path = fetch_vacancies(overrides)
    except (HHApiError, ValueError) as exc:
        typer.secho(f"Ошибка: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    count = json.loads(path.read_text(encoding="utf-8"))["count"]
    typer.secho(f"Готово: {count} вакансий сохранено в {path}", fg=typer.colors.GREEN)

    if no_details:
        return
    try:
        # С --region описания догружаются только для вакансий этих регионов (AC 2.7).
        loaded, failed = fetch_details(region_ids=region or None)
    except HHApiError as exc:
        typer.secho(f"Ошибка при загрузке описаний: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    color = typer.colors.YELLOW if failed else typer.colors.GREEN
    typer.secho(f"Описания: загружено {loaded}, ошибок {failed}", fg=color)


@app.command()
def analyze(
    data: Optional[Path] = typer.Option(None, "--data", exists=True, dir_okay=False,
                                        help="Один файл вакансий; по умолчанию объединяются все data/raw_vacancies_*.json"),
    days: Optional[int] = typer.Option(None, "--days", min=1, help="Только вакансии, опубликованные за последние N дней"),
    area: Optional[str] = typer.Option(None, "--area", help="Только вакансии региона, например \"Минск\""),
    no_llm: bool = typer.Option(False, "--no-llm", help="Только поиск по словарю, без обращений к LLM"),
    llm_refresh: bool = typer.Option(False, "--llm-refresh", help="Заново обработать LLM вакансии, уже бывшие в кэше"),
) -> None:
    """Формирует отчёт о востребованных навыках reports/market_skills_summary.md (US-03).

    Навыки вне словаря довыявляются LLM из llm_providers.vacancy_analysis (profile/preferences.json),
    затем навыки рынка сравниваются с profile/my_cv.md через LLM из llm_providers.cv_processing (AC 3.3).
    """
    try:
        path, count, warnings = run_analysis(data, days, area, use_llm=not no_llm, llm_refresh=llm_refresh)
    except (FileNotFoundError, ValueError, LLMError) as exc:
        typer.secho(f"Ошибка: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    for warning in warnings:
        typer.secho(f"Внимание: {warning}", fg=typer.colors.YELLOW)
    typer.secho(f"Готово: проанализировано {count} вакансий, отчёт — {path}", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
