"""Запись файлов без обрывков: прерванная команда оставляет прежний файл целым (ADR-003, AC 6.7)."""

from pathlib import Path


def write_text_atomic(path: Path, text: str) -> None:
    """Записывает текст через временный файл рядом с целевым и заменяет целевой одной операцией.

    Временный файл — «<имя>.tmp» (например, cl_123.md.tmp): он не попадает под шаблоны
    raw_vacancies_*.json и cl_*.md, поэтому не сбивает поиск выгрузок и нумерацию версий.
    Если процесс прерван во время записи, целевой файл остаётся прежним (или его ещё нет).
    """
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
