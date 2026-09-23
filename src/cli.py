"""Интерфейс командной строки.

Запуск из корня проекта: python -m src.cli <команда> [опции]
"""

import json
import logging
from typing import Any, Optional

import typer

from src.hh_client import HHApiError, fetch_vacancies

app = typer.Typer(help="Job Market Analyzer: сбор и анализ вакансий BA/SA.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Job Market Analyzer: сбор и анализ вакансий BA/SA."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")


@app.command()
def fetch(
    role: Optional[list[str]] = typer.Option(None, "--role", help="Роль для поиска (можно несколько раз)"),
    region: Optional[list[int]] = typer.Option(None, "--region", help="ID региона HH: 113 — Россия, 16 — Беларусь (можно несколько раз)"),
    experience: Optional[list[str]] = typer.Option(None, "--experience", help="noExperience / between1And3 / between3And6 / moreThan6 (можно несколько раз)"),
    employment: Optional[list[str]] = typer.Option(None, "--employment", help="full / part / project (можно несколько раз)"),
    schedule: Optional[list[str]] = typer.Option(None, "--schedule", help="office / hybrid / remote (можно несколько раз)"),
    min_salary: Optional[int] = typer.Option(None, "--min-salary", help="Минимальная зарплата (валюта — currency из профиля)"),
) -> None:
    """Выгружает вакансии с HH.ru в data/raw_vacancies_{timestamp}.json (US-02).

    Без опций используются search_settings из profile/preferences.json,
    переданные опции их переопределяют.
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


if __name__ == "__main__":
    app()
