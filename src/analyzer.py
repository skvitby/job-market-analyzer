"""Парсинг навыков и Gap-анализ (US-03).

Навыки ищутся по словарю analytical_skills_dictionary из profile/preferences.json
(AC 1.2, AC 3.1) в полном описании вакансии из кэша data/details/, а если его нет —
в сниппетах requirement / responsibility. Дополнительно учитываются key_skills из HH.
Навыки вне словаря довыявляются через LLM (задача vacancy_analysis, AC 1.5) по полным
описаниям; результат кэшируется в data/llm_skills_cache.json.
"""

import hashlib
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
from src.text_utils import SKILL, VACANCY, count, plural

logger = logging.getLogger(__name__)

REPORTS_DIR = PROJECT_ROOT / "reports"
REPORT_PATH = REPORTS_DIR / "market_skills_summary.md"
LLM_CACHE_PATH = DATA_DIR / "llm_skills_cache.json"
TOP_UNMATCHED = 20  # сколько key_skills вне словаря показывать в отчёте
TOP_LLM = 30        # сколько навыков, найденных LLM, показывать в отчёте

CV_PATH = PROJECT_ROOT / "profile" / "my_cv.md"
CV_CACHE_PATH = DATA_DIR / "cv_match_cache.json"
CV_MIN_SHARE = 0.05  # навыки словаря для сравнения с резюме — встречаются не менее чем в 5% вакансий
CV_MIN_LLM = 2       # навыки, найденные LLM, — не менее чем в 2 вакансиях
CV_CACHE_VERSION = 2  # меняется при изменении формата кэша сравнения с резюме — старый кэш не используется

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
    Короткие русские аббревиатуры из заглавных букв (до 3 символов: ТЗ, ФТ, ПСИ, ПМИ) — тоже
    целым словом, иначе «ПСИ» находилось бы внутри «психология».
    """
    parts = []
    for word in _WORD_SPLIT_RE.split(term.strip()):
        is_abbreviation = word.isupper() and len(word) <= 3
        suffix = "[а-яё]*" if _CYRILLIC_END_RE.search(word) and not is_abbreviation else ""
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
    logger.info("LLM %s/%s: отправляю %s, %s", settings.provider, settings.model,
                count(len(pending), *VACANCY), count(len(batches), "запрос", "запроса", "запросов"))
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




def excluded_matcher(excluded: list[str]) -> Optional[re.Pattern]:
    """Регулярное выражение для исключённых навыков (excluded_skills, AC 1.2) или None, если список пуст.

    Поиск тот же, что у словаря: «1С» отсекает и «1С программирование», «SAP» — и «SAP BW».
    """
    terms = [str(t) for t in excluded or [] if str(t).strip()]
    if not terms:
        return None
    (_, pattern), = build_matchers({terms[0]: terms[1:]})
    return pattern


def _is_excluded(name: str, excluded: Optional[re.Pattern]) -> bool:
    return bool(excluded and excluded.search(name))


def collect_stats(vacancies: list[dict[str, Any]], dictionary: dict[str, list[str]],
                  llm_cache: Optional[dict[str, dict[str, Any]]],
                  excluded: Optional[re.Pattern] = None) -> dict[str, Any]:
    """Считает частоты навыков: по словарю и key_skills (AC 3.2) и по результатам LLM (AC 3.1).

    excluded — исключённые навыки (excluded_skills): не попадают ни в один раздел отчёта
    и в сравнение с резюме.
    """
    matchers = [(skill, pattern) for skill, pattern in build_matchers(dictionary)
                if not _is_excluded(skill, excluded)]
    skill_counts: Counter[str] = Counter()
    unmatched: Counter[str] = Counter()
    unmatched_names: dict[str, str] = {}
    full_count = 0
    for vacancy in vacancies:
        detail = load_detail(str(vacancy["id"]))
        text, is_full = vacancy_text(vacancy, detail)
        full_count += is_full
        skills = match_skills(text, matchers)
        for key_skill in (detail or {}).get("key_skills") or []:
            found = match_skills(key_skill, matchers)
            skills |= found
            if not found and not _is_excluded(key_skill, excluded):
                key = key_skill.strip().lower()
                unmatched[key] += 1
                unmatched_names.setdefault(key, key_skill.strip())
        skill_counts.update(skills)  # навык учитывается не более одного раза на вакансию

    llm_counts: Counter[str] = Counter()
    llm_names: dict[str, Counter[str]] = {}
    llm_models: Counter[str] = Counter()
    llm_processed = 0
    for vacancy in vacancies if llm_cache is not None else []:
        entry = llm_cache.get(str(vacancy["id"]))
        if not entry:
            continue
        llm_processed += 1
        llm_models[f"{entry['provider']} / {entry['model']}"] += 1
        found_llm: set[str] = set()
        for raw in entry["skills"]:
            name = _normalize_llm_skill(raw)
            if not name or match_skills(name, matchers) or _is_excluded(name, excluded):
                continue  # навык уже учтён словарём или исключён — в разделе LLM его не показываем
            key = name.lower()
            found_llm.add(key)
            llm_names.setdefault(key, Counter())[name] += 1
        llm_counts.update(found_llm)

    return {
        "total": len(vacancies), "full_count": full_count, "skill_counts": skill_counts,
        "unmatched": unmatched, "unmatched_names": unmatched_names,
        "llm_counts": llm_counts, "llm_processed": llm_processed, "llm_models": llm_models,
        "llm_names": {key: names.most_common(1)[0][0] for key, names in llm_names.items()},
    }


# --- Сравнение с резюме (AC 3.3) ---

CV_INSTRUCTION = """Ты помогаешь бизнес/системному аналитику сравнить своё резюме с требованиями рынка.
Для КАЖДОГО навыка из списка определи, есть ли он в резюме, и верни статус:
- "skills_section" — навык указан в разделе «Навыки» резюме (с учётом синонимов и вариантов написания:
  Postgres = PostgreSQL, Confluence = Atlassian Confluence, Excel = MS Excel);
- "experience_only" — навык явно упомянут в опыте работы, обязанностях или «Обо мне», но НЕ в разделе «Навыки»;
- "missing" — в резюме навыка нет.

Правила:
- Засчитывай только явные упоминания навыка или его синонима. Не делай выводов из общих фраз
  (опыт в EdTech не означает знания Kafka).
- quote — ДОСЛОВНЫЙ короткий фрагмент резюме (до 100 символов), подтверждающий навык; для "missing" — пустая строка.
- Верни результат по каждому навыку из списка, название навыка — как в списке. Формат — JSON."""

CV_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "skills": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "skill": {"type": "string"},
                    "status": {"type": "string", "enum": ["skills_section", "experience_only", "missing"]},
                    "quote": {"type": "string"},
                },
                "required": ["skill", "status", "quote"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["skills"],
    "additionalProperties": False,
}

CV_STATUS_LABELS = {
    "skills_section": ("Да", "✅ Matches"),
    "experience_only": ("Только в опыте", "🟡 Добавить в «Навыки»"),
    "missing": ("Нет", "⚠️ Gap to address"),
}


def cv_candidates(stats: dict[str, Any]) -> list[dict[str, Any]]:
    """Навыки для сравнения с резюме: словарные с долей ≥ CV_MIN_SHARE и найденные LLM в ≥ CV_MIN_LLM вакансиях."""
    total = stats["total"]
    candidates = [{"skill": skill, "share": count / total, "source": "dictionary"}
                  for skill, count in stats["skill_counts"].most_common() if count / total >= CV_MIN_SHARE]
    processed = stats["llm_processed"]
    candidates += [{"skill": stats["llm_names"][key], "share": count / processed, "source": "llm"}
                   for key, count in stats["llm_counts"].items() if count >= CV_MIN_LLM]
    # При равной доле — по названию, чтобы порядок не зависел от случайного хэширования строк между запусками.
    return sorted(candidates, key=lambda c: (-c["share"], c["skill"].lower()))


def _normalize_text(text: str) -> str:
    """Текст для сверки цитат: без markdown-разметки, в нижнем регистре, с одиночными пробелами."""
    return re.sub(r"\s+", " ", re.sub(r"[*_#`>]", "", text)).strip().lower()


def cv_skills_section(cv_text: str) -> Optional[str]:
    """Раздел «Навыки» резюме: от заголовка «Навыки» до следующего заголовка того же или более высокого уровня."""
    lines = cv_text.splitlines()
    for i, line in enumerate(lines):
        match = re.match(r"^(#+)\s*Навыки\s*$", line.strip(), re.IGNORECASE)
        if not match:
            continue
        level = len(match.group(1))
        end = next((j for j in range(i + 1, len(lines))
                    if (m := re.match(r"^(#+)\s", lines[j].strip())) and len(m.group(1)) <= level), len(lines))
        return "\n".join(lines[i + 1:end])
    return None


_QUOTE_SPLIT_RE = re.compile(r"[;,/()«»\"…]|\.\.\.")
CV_STATUS_RANK = {"missing": 0, "experience_only": 1, "skills_section": 2}


def _quote_found(quote: str, text_norm: str) -> bool:
    """Цитата подтверждается, если каждый её фрагмент (между ; , / и т.п.) есть в тексте.

    Модель на длинных списках склеивает цитату из несмежных пунктов («BPMN; Моделирование
    бизнес-процессов»), поэтому дословного совпадения всей цитаты не требуем.
    """
    fragments = [f.strip() for f in _QUOTE_SPLIT_RE.split(_normalize_text(quote))]
    fragments = [f for f in fragments if len(f) >= 2]
    return bool(fragments) and all(f in text_norm for f in fragments)


def verify_cv_results(items: list[dict[str, Any]], cv_text: str) -> tuple[list[dict[str, Any]], int]:
    """Проверяет цитаты модели по тексту резюме, чтобы исключить выдуманные совпадения.

    Цитата не найдена в резюме -> статус "missing" (засчитываем только подтверждённое).
    Статус "skills_section", но цитата вне раздела «Навыки» -> "experience_only".
    Возвращает проверенные результаты и число исправленных статусов.
    """
    cv_norm = _normalize_text(cv_text)
    section = cv_skills_section(cv_text)
    section_norm = _normalize_text(section) if section is not None else None
    fixed = 0
    checked = []
    for item in items:
        status, quote = item["status"], item.get("quote", "")
        if status != "missing":
            if not _quote_found(quote, cv_norm):
                status, fixed = "missing", fixed + 1
            elif status == "skills_section" and section_norm is not None and not _quote_found(quote, section_norm):
                status, fixed = "experience_only", fixed + 1
        checked.append({**item, "status": status, "quote": quote if status != "missing" else ""})
    return checked, fixed


def dictionary_cv_status(skill: str, dictionary: dict[str, list[str]], cv_text: str) -> tuple[str, str]:
    """Статус навыка словаря в резюме по точному поиску с синонимами (как AC 3.1) и найденный фрагмент.

    Раздел «Навыки» -> "skills_section", остальной текст -> "experience_only", нет -> "missing".
    Для навыков вне словаря (найденных LLM) точного поиска нет -> "missing".
    """
    if skill not in dictionary:
        return "missing", ""
    (_, pattern), = build_matchers({skill: dictionary[skill]})
    section = cv_skills_section(cv_text)
    for text, status in ((section, "skills_section"), (cv_text, "experience_only")):
        if text and (match := pattern.search(text)):
            line = text[text.rfind("\n", 0, match.start()) + 1:].split("\n", 1)[0]
            # Фрагмент — пункт списка между «;», в котором найден навык (раздел «Навыки» — одна строка через «;»).
            fragment = next((part for part in line.split(";") if match.group(0) in part), line)
            fragment = re.sub(r"[*_#]", "", fragment).strip()
            return status, fragment if len(fragment) <= 100 else fragment[:97] + "..."
    return "missing", ""


_SKILL_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+")


def quote_mentions_skill(skill: str, quote: str) -> bool:
    """Цитата подтверждает навык, если в ней есть хотя бы одно слово из названия навыка (D-2).

    Длинные слова (от 4 букв) сравниваются по основе — первым 6 буквам, чтобы учитывались
    падежные формы («Интеграции» ← «интеграциям»); короткие (ПО, БД, ERP) — целым словом.
    """
    quote_low = quote.lower()
    for word in _SKILL_WORD_RE.findall(skill.lower()):
        if len(word) >= 4 and word[:6] in quote_low:
            return True
        if len(word) < 4 and re.search(rf"(?<!\w){re.escape(word)}(?!\w)", quote_low):
            return True
    return False


def merge_cv_statuses(items: list[dict[str, Any]], dictionary: dict[str, list[str]],
                      cv_text: str) -> tuple[list[dict[str, Any]], int]:
    """Итоговый статус навыка в резюме (AC 3.3, исправление D-2).

    Навыки словаря — только точный поиск по резюме с синонимами: ответ LLM для них не учитывается,
    поэтому их статус стабилен и не зависит от догадок модели. Навыки вне словаря (найденные LLM) —
    ответ LLM, если цитата содержит слово из названия навыка; иначе навык считается отсутствующим.
    Возвращает итоговые статусы и число отклонённых ответов LLM по навыкам вне словаря.
    """
    merged, rejected = [], 0
    for item in items:
        if item["skill"] in dictionary:
            status, fragment = dictionary_cv_status(item["skill"], dictionary, cv_text)
            merged.append({**item, "status": status, "quote": fragment})
        elif item["status"] != "missing" and not quote_mentions_skill(item["skill"], item["quote"]):
            merged.append({**item, "status": "missing", "quote": ""})
            rejected += 1
        else:
            merged.append(item)
    return merged, rejected


def compare_with_cv(candidates: list[dict[str, Any]], dictionary: dict[str, list[str]],
                    cv_path: Path = CV_PATH, cache_path: Path = CV_CACHE_PATH) -> dict[str, Any]:
    """Сопоставляет навыки рынка с резюме (AC 3.3, NFR-2): точный поиск по словарю + LLM из cv_processing.

    LLM находит навыки вне словаря и нестандартные формулировки; её цитаты проверяются по резюме.
    Ответ LLM кэшируется по отпечатку (резюме + список навыков + модель): пока они не меняются,
    повторный analyze к LLM не обращается. Точный поиск по словарю выполняется при каждом запуске.
    """
    cv_text = cv_path.read_text(encoding="utf-8")
    settings = get_settings("cv_processing")
    skills = [c["skill"] for c in candidates]
    fingerprint = hashlib.sha256(json.dumps([CV_CACHE_VERSION, cv_text, sorted(skills), settings.provider, settings.model],
                                            ensure_ascii=False).encode("utf-8")).hexdigest()
    cache = load_llm_cache(cache_path)
    if cache.get("fingerprint") == fingerprint:
        logger.info("Сравнение с резюме: резюме и список навыков не менялись, используется кэш")
        return _finalize_cv_result(cache, dictionary, cv_text)

    logger.info("Сравнение с резюме: %s, LLM %s/%s", count(len(skills), *SKILL), settings.provider, settings.model)
    text = f"## Список навыков\n" + "\n".join(f"- {s}" for s in skills) + f"\n\n## Резюме\n{cv_text}"
    answer = ask_json("cv_processing", CV_INSTRUCTION, text, CV_SCHEMA)

    by_skill = {str(item.get("skill", "")).strip().lower(): item for item in answer["skills"]}
    items = []
    for skill in skills:
        item = by_skill.get(skill.lower())
        if item is None:
            logger.warning("LLM не вернула результат по навыку «%s» — считаю его отсутствующим", skill)
            item = {"status": "missing", "quote": ""}
        status = item.get("status") if item.get("status") in CV_STATUS_LABELS else "missing"
        items.append({"skill": skill, "status": status, "quote": str(item.get("quote", ""))})

    # В кэше — сырой ответ LLM: проверка цитат и точный поиск по словарю выполняются при каждом запуске.
    raw = {"fingerprint": fingerprint, "provider": settings.provider, "model": settings.model,
           "processed_at": datetime.now().astimezone().isoformat(timespec="seconds"), "items": items}
    save_llm_cache(raw, cache_path)
    return _finalize_cv_result(raw, dictionary, cv_text)


def _finalize_cv_result(raw: dict[str, Any], dictionary: dict[str, list[str]], cv_text: str) -> dict[str, Any]:
    """Проверяет цитаты LLM по резюме и объединяет с точным поиском по словарю."""
    llm_items = [i for i in raw["items"] if i["skill"] not in dictionary]
    verified, fixed = verify_cv_results(llm_items, cv_text)
    verified_by_skill = {i["skill"]: i for i in verified}
    items = [verified_by_skill.get(i["skill"], i) for i in raw["items"]]
    items, rejected = merge_cv_statuses(items, dictionary, cv_text)
    fixed += rejected
    if fixed:
        logger.warning("Сравнение с резюме: ответов LLM по навыкам вне словаря не засчитано или понижено — %d "
                       "(цитата не найдена в резюме, не подтверждает навык или найдена вне раздела «Навыки»)", fixed)
    return {**raw, "fixed": fixed, "items": items}


# --- Отчёт ---

def _llm_section(stats: dict[str, Any], llm_error: Optional[str]) -> list[str]:
    """Раздел отчёта с навыками вне словаря, найденными LLM (AC 3.1)."""
    lines = ["", f"## Навыки вне словаря, найденные LLM (топ-{TOP_LLM})", ""]
    if llm_error:
        lines += [f"> [!warning] Обработка LLM прервана: {llm_error}. Показаны результаты из кэша.", ""]
    processed = stats["llm_processed"]
    if not processed:
        return lines + ["Нет вакансий, обработанных LLM (нужны полные описания — команда `fetch`)."]

    lines += [f"> [!info] Получено моделью {', '.join(f'`{m}`' for m in stats['llm_models'])} по {processed} из "
              f"{stats['total']} {plural(stats['total'], 'вакансии', 'вакансий', 'вакансий')} (только с полным описанием). Это интерпретация модели, а не точный "
              "поиск: частые навыки — кандидаты на пополнение словаря.", "",
              "| # | Навык | Вакансий | Доля |", "|---|---|---|---|"]
    top = sorted(stats["llm_counts"].items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_LLM]
    for rank, (key, count) in enumerate(top, start=1):
        lines.append(f"| {rank} | {stats['llm_names'][key]} | {count} | {count / processed:.0%} |")
    return lines


def _cv_section(candidates: list[dict[str, Any]], cv_result: Optional[dict[str, Any]],
                cv_note: Optional[str]) -> list[str]:
    """Раздел отчёта «Сравнение с резюме» (AC 3.3)."""
    lines = ["", "## Сравнение с резюме", ""]
    if cv_result is None:
        return lines + [f"> [!warning] {cv_note}"]

    share = {c["skill"]: c for c in candidates}
    items = [i for i in cv_result["items"] if i["skill"] in share]
    by_status = Counter(i["status"] for i in items)
    total_share = sum(share[i["skill"]]["share"] for i in items) or 1
    covered_share = sum(share[i["skill"]]["share"] for i in items if i["status"] != "missing")
    lines += [
        f"> [!info] Сопоставлено моделью `{cv_result['provider']} / {cv_result['model']}` "
        f"({cv_result['processed_at'][:16].replace('T', ' ')}). Навыки: из словаря с долей ≥ {CV_MIN_SHARE:.0%} "
        f"и найденные LLM в ≥ {CV_MIN_LLM} {plural(CV_MIN_LLM, 'вакансии', 'вакансиях', 'вакансиях')} (помечены \\*). Навыки словаря ищутся в резюме только точно "
        "(с синонимами); навыки \\* — по ответу LLM, если цитата содержит слово из названия навыка. Каждое совпадение "
        "подтверждено фрагментом резюме"
        + (f"; ответов LLM по навыкам \\*, не засчитанных или пониженных проверкой, — {cv_result['fixed']}."
           if cv_result["fixed"] else "."),
        "",
        f"**Итого:** {count(len(items), *SKILL)} — ✅ {by_status['skills_section']} в разделе «Навыки», "
        f"🟡 {by_status['experience_only']} только в опыте, "
        f"⚠️ {count(by_status['missing'], 'пробел', 'пробела', 'пробелов')}. "
        f"Покрытие с учётом частоты на рынке — **{covered_share / total_share:.0%}**.",
        "",
        "| Навык | Частота на рынке | Наличие в резюме | Статус | Где в резюме |",
        "|---|---|---|---|---|",
    ]
    for item in items:
        c = share[item["skill"]]
        presence, status = CV_STATUS_LABELS[item["status"]]
        quote = item["quote"].replace("|", "/").replace("\n", " ")
        name = item["skill"] + (" \\*" if c["source"] == "llm" else "")
        lines.append(f"| {name} | {c['share']:.0%} | {presence} | {status} | {f'«{quote}»' if quote else '—'} |")
    return lines


def build_report(stats: dict[str, Any], dictionary: dict[str, list[str]], vacancies: list[dict[str, Any]],
                 files: list[Path], days: Optional[int], area: Optional[str], use_llm: bool,
                 llm_error: Optional[str] = None, candidates: Optional[list[dict[str, Any]]] = None,
                 cv_result: Optional[dict[str, Any]] = None, cv_note: Optional[str] = None) -> str:
    """Формирует Markdown-отчёт (AC 3.2): топ навыков, раздел LLM (AC 3.1), сравнение с резюме (AC 3.3)."""
    total, full_count = stats["total"], stats["full_count"]
    skill_counts, unmatched = stats["skill_counts"], stats["unmatched"]
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
        f"- **Период публикации (с учётом поднятий):** {_format_date(min(dates, default=None))} — "
        f"{_format_date(max(dates, default=None))}",
        f"- **Вакансий:** {total} (по полному описанию — {full_count}, по сниппетам — {total - full_count})",
    ]
    if use_llm:
        lines.append("- **Сравнение с резюме:** см. раздел [[#Сравнение с резюме]]")
    if total and full_count < total:
        lines.append("")
        lines.append(f"> [!warning] {count(total - full_count, *VACANCY)} без полного описания: в сниппетах часть "
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
            lines.append(f"| {stats['unmatched_names'][key]} | {count} |")

    if use_llm:
        lines += _llm_section(stats, llm_error)
        lines += _cv_section(candidates or [], cv_result, cv_note)

    return "\n".join(lines) + "\n"


def analyze(data_path: Optional[Path] = None, days: Optional[int] = None, area: Optional[str] = None,
            use_llm: bool = True, llm_refresh: bool = False) -> tuple[Path, int, list[str]]:
    """Формирует reports/market_skills_summary.md.

    use_llm=False — только словарь (--no-llm), без LLM и сравнения с резюме;
    llm_refresh — заново обработать вакансии из кэша LLM.
    Возвращает путь к отчёту, число вакансий и предупреждения (прерванные LLM-этапы).
    """
    prefs = load_preferences()
    dictionary = normalize_dictionary(prefs["analytical_skills_dictionary"])
    excluded = excluded_matcher(prefs["excluded_skills"])
    all_vacancies, files = load_vacancies(data_path)
    vacancies = filter_vacancies(all_vacancies, days, area)
    if not vacancies:
        if area and not filter_vacancies(all_vacancies, None, area):
            # Город не найден вовсе — подсказываем, какие есть в данных (Z-3).
            cities = Counter(v.get("area") or "—" for v in all_vacancies).most_common(10)
            raise ValueError(f"Город «{area}» не найден в выгрузках. --area — название города, как в вакансиях HH, "
                             f"а не ID региона. Есть: {', '.join(f'{c} ({n})' for c, n in cities)}")
        raise ValueError("После применения фильтров не осталось вакансий")
    logger.info("Анализирую %s, словарь — %s", count(len(vacancies), *VACANCY), count(len(dictionary), *SKILL))

    warnings: list[str] = []
    llm_cache, llm_error = extract_llm_skills(vacancies, llm_refresh) if use_llm else (None, None)
    if llm_error:
        warnings.append(f"LLM-анализ вакансий прерван: {llm_error}")
    stats = collect_stats(vacancies, dictionary, llm_cache, excluded)

    candidates, cv_result, cv_note = None, None, None
    if use_llm:
        candidates = cv_candidates(stats)
        if not CV_PATH.exists():
            cv_note = f"Файл резюме {CV_PATH.relative_to(PROJECT_ROOT)} не найден — сравнение пропущено."
        else:
            try:
                cv_result = compare_with_cv(candidates, dictionary)
            except LLMError as exc:
                cv_note = f"Сравнение с резюме не выполнено: {exc}"
                warnings.append(cv_note)

    REPORTS_DIR.mkdir(exist_ok=True)
    report = build_report(stats, dictionary, vacancies, files, days, area, use_llm,
                          llm_error, candidates, cv_result, cv_note)
    REPORT_PATH.write_text(report, encoding="utf-8")
    logger.info("Отчёт сохранён в %s", REPORT_PATH)
    return REPORT_PATH, len(vacancies), warnings
