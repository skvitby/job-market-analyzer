"""Парсинг навыков и Gap-анализ (US-03).

Навыки ищутся по словарю analytical_skills_dictionary из profile/preferences.json
(AC 1.2, AC 3.1) в полном описании вакансии из кэша data/details/, а если его нет —
в сниппетах requirement / responsibility. Дополнительно учитываются key_skills из HH.
Навыки вне словаря довыявляются через LLM (задача vacancy_analysis, AC 1.5) по полным
описаниям; результат кэшируется в data/llm_skills_cache.json.
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
from src.llm_service import LLMError, ask_json, get_settings

logger = logging.getLogger(__name__)

REPORTS_DIR = PROJECT_ROOT / "reports"
REPORT_PATH = REPORTS_DIR / "market_skills_summary.md"
LLM_CACHE_PATH = DATA_DIR / "llm_skills_cache.json"
TOP_UNMATCHED = 20  # сколько key_skills вне словаря показывать в отчёте
TOP_LLM = 30        # сколько навыков, найденных LLM, показывать в отчёте

LLM_INSTRUCTION = """Ты анализируешь вакансии бизнес- и системных аналитиков.
Для каждой вакансии выпиши навыки, которые требуются от кандидата: инструменты, технологии,
нотации, методологии, стандарты, предметные знания. Учитывай и обязательные требования, и «будет плюсом».

Правила:
- Короткие канонические названия (1–4 слова), как принято в индустрии: «Camunda», «ГОСТ 34», «Power BI»,
  «Банковский домен» — а не «опыт работы с Camunda» или «знание стандарта ГОСТ 34».
- Не включай название должности, личные качества, условия работы и то, что предлагает компания.
- Не путай продукт или сферу компании с навыком: если компания разрабатывает CRM — это не навык;
  если требуется опыт работы с CRM-системами — навык.
- Каждый навык в вакансии — один раз. Если навыков нет — пустой список.
- Верни результат для каждой вакансии с её id в формате JSON."""

LLM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "vacancies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "skills": {"type": "array", "items": {"type": "string"}}},
                "required": ["id", "skills"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["vacancies"],
    "additionalProperties": False,
}

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


def load_llm_cache(path: Path = LLM_CACHE_PATH) -> dict[str, dict[str, Any]]:
    """Кэш навыков, найденных LLM: {id: {skills, provider, model, processed_at}}."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("Повреждён кэш LLM %s (%s), начинаю заново", path, exc)
        return {}


def save_llm_cache(cache: dict[str, dict[str, Any]], path: Path = LLM_CACHE_PATH) -> None:
    """Сохраняет кэш LLM через временный файл, чтобы прерывание не повредило оплаченные результаты."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _llm_payload(vacancy: dict[str, Any], description: str) -> str:
    """Текст одной вакансии для отправки в LLM."""
    return f"### Вакансия id={vacancy['id']}\nНазвание: {vacancy.get('name', '')}\nОписание:\n{description}"


def extract_llm_skills(vacancies: list[dict[str, Any]], refresh: bool = False,
                       cache_path: Path = LLM_CACHE_PATH) -> tuple[dict[str, dict[str, Any]], Optional[str]]:
    """Довыявляет навыки через LLM по полным описаниям (AC 3.1) и возвращает кэш и текст ошибки.

    В LLM уходят только вакансии с полным описанием, которых ещё нет в кэше
    (refresh=True — обработать заново текущей моделью). Кэш сохраняется после каждого пакета.
    При ошибке LLM этап останавливается, уже полученные результаты сохраняются.
    """
    cache = load_llm_cache(cache_path)
    settings = get_settings("vacancy_analysis")
    pending = []
    for vacancy in vacancies:
        vacancy_id = str(vacancy["id"])
        if vacancy_id in cache and not refresh:
            continue
        detail = load_detail(vacancy_id)
        if detail and detail.get("description"):
            pending.append((vacancy, "\n".join(detail["description"])))
    if not pending:
        logger.info("LLM: новых вакансий для обработки нет, используется кэш")
        return cache, None

    batches = [pending[i:i + settings.batch_size] for i in range(0, len(pending), settings.batch_size)]
    logger.info("LLM %s/%s: отправляю %d вакансий, %d запросов",
                settings.provider, settings.model, len(pending), len(batches))
    for n, batch in enumerate(batches, start=1):
        text = "\n\n".join(_llm_payload(vacancy, description) for vacancy, description in batch)
        try:
            answer = ask_json("vacancy_analysis", LLM_INSTRUCTION, text, LLM_SCHEMA)
        except LLMError as exc:
            logger.error("LLM: остановлено на пакете %d/%d: %s", n, len(batches), exc)
            return cache, str(exc)

        sent = {str(vacancy["id"]) for vacancy, _ in batch}
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        for item in answer["vacancies"]:
            vacancy_id = str(item.get("id", ""))
            if vacancy_id in sent:
                cache[vacancy_id] = {"skills": [str(s) for s in item.get("skills", [])],
                                     "provider": settings.provider, "model": settings.model, "processed_at": now}
                sent.discard(vacancy_id)
        if sent:
            logger.warning("LLM не вернул результат по вакансиям %s — обработаются при следующем запуске",
                           ", ".join(sorted(sent)))
        save_llm_cache(cache, cache_path)
        logger.info("LLM: пакет %d/%d готов", n, len(batches))
    return cache, None


def _normalize_llm_skill(name: str) -> str:
    """Приводит название навыка от LLM к единому виду: без лишних пробелов и знаков по краям."""
    return re.sub(r"\s+", " ", name).strip(" .,;:«»\"'")


def _format_date(value: Optional[datetime]) -> str:
    return f"{value:%d.%m.%Y}" if value else "—"


def _llm_section(vacancies: list[dict[str, Any]], matchers: list[tuple[str, re.Pattern]],
                 llm_cache: dict[str, dict[str, Any]], llm_error: Optional[str]) -> list[str]:
    """Раздел отчёта с навыками вне словаря, найденными LLM (AC 3.1)."""
    counts: Counter[str] = Counter()
    names: dict[str, Counter[str]] = {}
    models: Counter[str] = Counter()
    processed = 0
    for vacancy in vacancies:
        entry = llm_cache.get(str(vacancy["id"]))
        if not entry:
            continue
        processed += 1
        models[f"{entry['provider']} / {entry['model']}"] += 1
        found: set[str] = set()
        for raw in entry["skills"]:
            name = _normalize_llm_skill(raw)
            if not name or match_skills(name, matchers):
                continue  # навык уже учтён словарём — в разделе LLM его не показываем
            key = name.lower()
            found.add(key)
            names.setdefault(key, Counter())[name] += 1
        counts.update(found)  # навык учитывается не более одного раза на вакансию

    lines = ["", f"## Навыки вне словаря, найденные LLM (топ-{TOP_LLM})", ""]
    if llm_error:
        lines += [f"> [!warning] Обработка LLM прервана: {llm_error}. Показаны результаты из кэша.", ""]
    if not processed:
        return lines + ["Нет вакансий, обработанных LLM (нужны полные описания — команда `fetch`)."]

    lines += [f"> [!info] Получено моделью {', '.join(f'`{m}`' for m in models)} по {processed} из "
              f"{len(vacancies)} вакансий (только с полным описанием). Это интерпретация модели, а не точный "
              "поиск: частые навыки — кандидаты на пополнение словаря.", "",
              "| # | Навык | Вакансий | Доля |", "|---|---|---|---|"]
    for rank, (key, count) in enumerate(counts.most_common(TOP_LLM), start=1):
        lines.append(f"| {rank} | {names[key].most_common(1)[0][0]} | {count} | {count / processed:.0%} |")
    return lines


def build_report(vacancies: list[dict[str, Any]], dictionary: dict[str, list[str]],
                 files: list[Path], days: Optional[int], area: Optional[str],
                 llm_cache: Optional[dict[str, dict[str, Any]]] = None, llm_error: Optional[str] = None) -> str:
    """Считает частоту навыков и формирует Markdown-отчёт (AC 3.2).

    llm_cache — результаты LLM (None — анализ без LLM, флаг --no-llm).
    """
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

    if llm_cache is not None:
        lines += _llm_section(vacancies, matchers, llm_cache, llm_error)

    return "\n".join(lines) + "\n"


def analyze(data_path: Optional[Path] = None, days: Optional[int] = None, area: Optional[str] = None,
            use_llm: bool = True, llm_refresh: bool = False) -> tuple[Path, int, Optional[str]]:
    """Формирует reports/market_skills_summary.md.

    use_llm=False — только словарь (--no-llm); llm_refresh — заново обработать вакансии из кэша LLM.
    Возвращает путь к отчёту, число вакансий и текст ошибки LLM (если этап был прерван).
    """
    dictionary = normalize_dictionary(load_preferences()["analytical_skills_dictionary"])
    vacancies, files = load_vacancies(data_path)
    vacancies = filter_vacancies(vacancies, days, area)
    if not vacancies:
        raise ValueError("После применения фильтров не осталось вакансий")
    logger.info("Анализирую %d вакансий, словарь — %d навыков", len(vacancies), len(dictionary))

    llm_cache, llm_error = extract_llm_skills(vacancies, llm_refresh) if use_llm else (None, None)
    REPORTS_DIR.mkdir(exist_ok=True)
    report = build_report(vacancies, dictionary, files, days, area, llm_cache, llm_error)
    REPORT_PATH.write_text(report, encoding="utf-8")
    logger.info("Отчёт сохранён в %s", REPORT_PATH)
    return REPORT_PATH, len(vacancies), llm_error
