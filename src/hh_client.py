"""Интеграция с API HH.ru (US-02).

Запросы к HH выполняются через внешний curl (путь задаётся в HH_CURL_PATH),
а не через requests: так трафик к HH можно исключить из VPN по приложению,
оставив остальной Python (в том числе Claude API) внутри VPN.
"""

import html
import json
import logging
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

from dotenv import load_dotenv

from src.config import PROJECT_ROOT, load_preferences
from src.text_utils import VACANCY, count, duration

logger = logging.getLogger(__name__)

API_URL = "https://api.hh.ru"
DATA_DIR = PROJECT_ROOT / "data"
DETAILS_DIR = DATA_DIR / "details"  # кэш полных описаний вакансий (AC 2.7)

PER_PAGE = 100            # максимум HH API
MAX_RESULTS = 2000        # HH отдаёт не больше 2000 вакансий на один поиск
MAX_RETRIES = 3           # повторы при 429/503 и сетевых сбоях
RETRY_BASE_DELAY = 10.0   # пауза перед повтором: 10, 20, 30 сек
DEFAULT_DELAY = 3.0       # пауза между запросами (NFR-1)
MAX_PERIOD_DAYS = 30      # HH ищет вакансии не старше 30 дней
FETCH_OVERLAP = timedelta(hours=1)  # запас при догрузке, дубли убирает дедупликация
MAX_DETAIL_FAILURES = 5   # столько ошибок подряд при загрузке описаний — останавливаем этап

# Соответствие значений из preferences.json параметрам HH API (AC 2.5).
WORK_FORMAT_MAP = {"office": "ON_SITE", "hybrid": "HYBRID", "remote": "REMOTE"}
EMPLOYMENT_MAP = {"full": "FULL", "part": "PART", "project": "PROJECT"}
EXPERIENCE_VALUES = {"noExperience", "between1And3", "between3And6", "moreThan6"}


class HHApiError(RuntimeError):
    """Ошибка обращения к HH API с понятным пользователю описанием."""

    def __init__(self, message: str, code: Optional[str] = None) -> None:
        super().__init__(message)
        self.code = code  # HTTP-код ответа HH, если он был


def _as_list(value: Any) -> list:
    """Приводит значение настройки к списку: None -> [], строка -> [строка]."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _map_values(values: list, mapping: dict[str, str], setting: str) -> list[str]:
    """Переводит значения настройки в значения HH API, отклоняя неизвестные."""
    unknown = [v for v in values if v not in mapping]
    if unknown:
        raise ValueError(f"Неизвестные значения {setting}: {unknown}. Допустимые: {list(mapping)}")
    return [mapping[v] for v in values]


def build_search_params(settings: dict[str, Any], period_days: Optional[int] = None,
                        date_from: Optional[datetime] = None) -> list[tuple[str, Any]]:
    """Собирает параметры поиска HH API из search_settings (AC 2.1, 2.2, 2.5, 2.6).

    Все роли объединяются в один запрос через OR, поиск идёт по названию вакансии,
    слова из exclude_words исключаются через NOT. Глубина поиска задаётся либо
    period_days (за сколько дней), либо date_from (начиная с какого момента).
    Возвращается список пар, так как параметры area, experience и work_format
    передаются несколько раз.
    """
    roles = _as_list(settings.get("target_roles"))
    if not roles:
        raise ValueError("Не задан ни один target_role для поиска")

    text = " OR ".join(f"({role})" for role in roles)
    exclude = _as_list(settings.get("exclude_words"))
    if exclude:
        text = f"({text}) " + " ".join(f"NOT {word}" for word in exclude)

    params: list[tuple[str, Any]] = [("text", text), ("search_field", "name")]
    params += [("area", region["id"]) for region in _as_list(settings.get("regions"))]

    experience = _as_list(settings.get("experience_level"))
    unknown = [v for v in experience if v not in EXPERIENCE_VALUES]
    if unknown:
        raise ValueError(f"Неизвестные значения experience_level: {unknown}")
    params += [("experience", v) for v in experience]

    employment = _map_values(_as_list(settings.get("employment_type")), EMPLOYMENT_MAP, "employment_type")
    params += [("employment_form", v) for v in employment]

    work_formats = _map_values(_as_list(settings.get("schedule")), WORK_FORMAT_MAP, "schedule")
    params += [("work_format", v) for v in work_formats]

    if settings.get("min_salary"):
        params.append(("salary", settings["min_salary"]))
        if settings.get("currency"):
            params.append(("currency", settings["currency"]))
        params.append(("only_with_salary", "true"))

    if period_days is not None:
        if not 1 <= period_days <= MAX_PERIOD_DAYS:
            raise ValueError(f"Период поиска должен быть от 1 до {MAX_PERIOD_DAYS} дней, получено {period_days}")
        params.append(("period", period_days))
    if date_from is not None:
        params.append(("date_from", date_from.isoformat(timespec="seconds")))

    return params


def last_fetch_times() -> dict[int, datetime]:
    """Время последней выгрузки по каждому региону: {ID региона HH: fetched_at} (AC 2.6).

    Регионы выгрузки берутся из параметров поиска (area) в файле; для каждого региона —
    самый свежий файл, в котором он участвовал в поиске.
    """
    times: dict[int, datetime] = {}
    for path in sorted(DATA_DIR.glob("raw_vacancies_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logger.warning("Не удалось прочитать %s (%s), файл пропущен", path, exc)
            continue
        try:
            fetched_at = datetime.fromisoformat(payload["fetched_at"]).astimezone()
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning("Не удалось прочитать fetched_at из %s (%s), беру время изменения файла", path, exc)
            fetched_at = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
        for key, value in payload.get("search_params") or []:
            if key == "area":
                region_id = int(value)
                times[region_id] = max(times.get(region_id, fetched_at), fetched_at)
    return times


def last_fetch_time(region_ids: list[int]) -> tuple[Optional[datetime], dict[int, datetime]]:
    """Момент, с которого искать, чтобы ни один из регионов не потерял вакансии (AC 2.6).

    Возвращает самую раннюю из последних выгрузок по запрошенным регионам и сами эти даты.
    None — хотя бы по одному региону выгрузок ещё не было (первый запуск для него).
    """
    known = last_fetch_times()
    times = {region_id: known[region_id] for region_id in region_ids if region_id in known}
    if not region_ids or len(times) < len(region_ids):
        return None, times
    return min(times.values()), times


class HHClient:
    """Клиент HH API с паузой между запросами и повтором при 429/503 (NFR-1)."""

    def __init__(self, delay: float = DEFAULT_DELAY) -> None:
        load_dotenv(PROJECT_ROOT / ".env")
        self.token = os.getenv("HH_APP_TOKEN")
        self.user_agent = os.getenv("HH_USER_AGENT")
        self.curl = os.getenv("HH_CURL_PATH") or shutil.which("curl")
        if not self.token:
            raise HHApiError("В .env не задан HH_APP_TOKEN (токен приложения HH)")
        if not self.user_agent:
            raise HHApiError("В .env не задан HH_USER_AGENT")
        if not self.curl:
            raise HHApiError("curl не найден: укажите путь в HH_CURL_PATH в .env")
        self.delay = delay
        self._last_request = 0.0

    def _wait(self) -> None:
        """Выдерживает паузу между запросами."""
        pause = self.delay - (time.monotonic() - self._last_request)
        if pause > 0:
            time.sleep(pause)
        self._last_request = time.monotonic()

    def get(self, path: str, params: Optional[list[tuple[str, Any]]] = None) -> dict[str, Any]:
        """Выполняет GET-запрос к HH API и возвращает JSON-ответ."""
        # Кодируем параметры сами: иначе кириллица портится при передаче в curl.exe на Windows.
        url = f"{API_URL}{path}" + (f"?{urlencode(params)}" if params else "")
        cmd = [self.curl, "-s", "-w", "\n%{http_code}", url,
               "-H", f"User-Agent: {self.user_agent}",
               "-H", f"Authorization: Bearer {self.token}"]

        for attempt in range(1, MAX_RETRIES + 2):
            self._wait()
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
            body, _, code = result.stdout.rpartition("\n")

            if result.returncode == 0 and code == "200":
                return json.loads(body)

            retryable = result.returncode != 0 or code in ("429", "503")
            if retryable and attempt <= MAX_RETRIES:
                wait = RETRY_BASE_DELAY * attempt
                logger.warning("HH ответил %s (curl=%s), повтор %d/%d через %.0f сек",
                               code or "-", result.returncode, attempt, MAX_RETRIES, wait)
                time.sleep(wait)
                continue
            raise HHApiError(self._describe_error(code, body, result.returncode), code=code or None)

        raise HHApiError("Исчерпаны попытки запроса к HH")  # недостижимо, для линтера

    @staticmethod
    def _describe_error(code: str, body: str, returncode: int) -> str:
        """Формирует понятное описание ошибки HH API."""
        if returncode != 0:
            return f"curl завершился с кодом {returncode}: нет соединения с api.hh.ru"
        if "DDoS-Guard" in body:
            return "HH заблокировал IP (DDoS-Guard): проверьте, что curl исключён из VPN"
        if code == "403":
            return "HH вернул 403: проверьте HH_APP_TOKEN"
        if code == "404":
            return "HH вернул 404: вакансия не найдена"
        return f"HH вернул HTTP {code}: {body[:300]}"

    def search(self, params: list[tuple[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        """Выгружает все страницы поиска (до 2000 вакансий). Возвращает вакансии и found."""
        items: list[dict[str, Any]] = []
        page, pages, found = 0, 1, 0
        while page < pages and page * PER_PAGE < MAX_RESULTS:
            data = self.get("/vacancies", params + [("per_page", PER_PAGE), ("page", page)])
            items += data["items"]
            pages, found = data["pages"], data["found"]
            logger.info("Страница %d/%d, всего найдено %d", page + 1, pages, found)
            page += 1
        return items, found


def _split_by(params: list[tuple[str, Any]], key: str) -> list[list[tuple[str, Any]]]:
    """Разбивает запрос на несколько по значениям параметра key (если их больше одного)."""
    values = [v for k, v in params if k == key]
    if len(values) < 2:
        return []
    rest = [(k, v) for k, v in params if k != key]
    return [rest + [(key, v)] for v in values]


def _collect(client: HHClient, params: list[tuple[str, Any]], split_keys: list[str]) -> list[dict[str, Any]]:
    """Выгружает вакансии; если поиск упирается в лимит 2000, дробит его по регионам, затем по опыту."""
    items, found = client.search(params)
    if found <= MAX_RESULTS:
        return items
    for i, key in enumerate(split_keys):
        parts = _split_by(params, key)
        if parts:
            logger.info("Найдено %d > %d, разбиваю поиск по параметру %s", found, MAX_RESULTS, key)
            return [item for part in parts for item in _collect(client, part, split_keys[i + 1:])]
    logger.warning("По запросу найдено: %s, HH отдаёт только первые %d. Сузьте фильтры.", count(found, *VACANCY), MAX_RESULTS)
    return items


_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: Optional[str]) -> Optional[str]:
    """Убирает из сниппета HTML-теги подсветки (<highlighttext>)."""
    return _TAG_RE.sub("", text) if text else text


_BOLD_RE = re.compile(r"<(strong|b)\b[^>]*>(.*?)</\1\s*>", re.IGNORECASE | re.DOTALL)
_LIST_ITEM_RE = re.compile(r"<li\b[^>]*>", re.IGNORECASE)
_BLOCK_TAG_RE = re.compile(r"<\s*/?\s*(br|p|ul|ol|li|div|h\d)\b[^>]*>", re.IGNORECASE)
_INVISIBLE_RE = re.compile("[​‌‍⁠﻿]")


def _bold(match: re.Match) -> str:
    """<strong>текст</strong> -> **текст**; пробелы выносятся за пределы звёздочек."""
    inner = match.group(2)
    if not _TAG_RE.sub("", inner).strip():
        return inner
    lead = " " if inner[:1].isspace() else ""
    trail = " " if inner[-1:].isspace() else ""
    return f"{lead}**{inner.strip()}**{trail}"


def _html_to_paragraphs(text: Optional[str]) -> Optional[list[str]]:
    """Переводит HTML-описание вакансии в список абзацев в формате Markdown.

    Абзацы и переносы (<p>, <br>, <li> и т.п.) -> отдельные элементы списка,
    <strong>/<b> -> **жирный**, пункты списков -> "- пункт". Невидимые символы
    и пустые абзацы удаляются. Список удобно читать прямо в JSON-файле кэша.
    """
    if text is None:
        return None
    text = _INVISIBLE_RE.sub("", text)
    text = _BOLD_RE.sub(_bold, text).replace("****", "")
    text = _LIST_ITEM_RE.sub("\n- ", text)
    text = _BLOCK_TAG_RE.sub("\n", text)
    text = html.unescape(_TAG_RE.sub("", text)).replace("\xa0", " ")
    paragraphs = (re.sub(r"\s+", " ", line).strip() for line in text.splitlines())
    return [p for p in paragraphs if p and p not in ("-", "**")]


def to_record(item: dict[str, Any]) -> dict[str, Any]:
    """Оставляет поля вакансии из AC 2.4."""
    snippet = item.get("snippet") or {}
    return {
        "id": item["id"],
        "name": item["name"],
        "salary": item.get("salary"),
        "area": (item.get("area") or {}).get("name"),
        "employer": (item.get("employer") or {}).get("name"),
        "requirement": _clean(snippet.get("requirement")),
        "responsibility": _clean(snippet.get("responsibility")),
        "alternate_url": item.get("alternate_url"),
        "published_at": item.get("published_at"),
    }


def fetch_vacancies(overrides: Optional[dict[str, Any]] = None) -> Path:
    """Выгружает вакансии по search_settings и сохраняет в data/raw_vacancies_{timestamp}.json.

    overrides — значения из CLI-аргументов, имеют приоритет над preferences.json (AC 2.1, 2.2, 2.5).
    """
    settings = {**load_preferences()["search_settings"], **(overrides or {})}
    now = datetime.now().astimezone()
    region_ids = [int(region["id"]) for region in _as_list(settings.get("regions"))]
    last_fetch, region_times = last_fetch_time(region_ids)
    history = ", ".join(f"{rid} — {t:%d.%m %H:%M}" for rid, t in region_times.items())
    if last_fetch is None:
        # Первый запуск хотя бы для одного региона (AC 2.6): вакансии за initial_period_days дней.
        period_days, date_from = int(settings.get("initial_period_days") or 14), None
        new_regions = [str(rid) for rid in region_ids if rid not in region_times]
        logger.info("Первая выгрузка для регионов %s: ищу вакансии за последние %d дн.%s",
                    ", ".join(new_regions), period_days, f" (по остальным: {history})" if history else "")
    else:
        # Повторный запуск: с самой ранней из последних выгрузок по регионам, но не глубже, чем позволяет HH.
        period_days = None
        earliest = now - timedelta(days=MAX_PERIOD_DAYS)
        date_from = max(last_fetch - FETCH_OVERLAP, earliest)
        if last_fetch - FETCH_OVERLAP < earliest:
            logger.warning("Прошлая выгрузка была более %d дней назад: вакансии старше %s HH уже не отдаёт",
                           MAX_PERIOD_DAYS, f"{earliest:%d.%m}")
        logger.info("Ищу вакансии с %s (последние выгрузки по регионам: %s)", f"{date_from:%d.%m %H:%M}", history)
    params = build_search_params(settings, period_days, date_from)
    client = HHClient(delay=float(settings.get("request_delay_sec") or DEFAULT_DELAY))

    items = _collect(client, params, split_keys=["area", "experience"])
    records = list({item["id"]: to_record(item) for item in items}.values())  # без дублей
    # Новая вакансия — id, которого нет в прежних выгрузках (Z-5). Уже известные — поднятые работодателем
    # или попавшие в запас в 1 час на стыке с прошлой выгрузкой.
    known_ids = set(_all_vacancy_ids())
    new_count = sum(str(record["id"]) not in known_ids for record in records)
    logger.info("Результат поиска: %s — новых %d, уже известных %d (есть в прошлых выгрузках)",
                count(len(records), *VACANCY), new_count, len(records) - new_count)

    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / f"raw_vacancies_{now:%Y%m%d_%H%M%S}.json"
    payload = {
        "fetched_at": now.isoformat(timespec="seconds"),
        "period_days": period_days,
        "date_from": date_from.isoformat(timespec="seconds") if date_from else None,
        "search_params": [[k, v] for k, v in params],
        "count": len(records),
        "new_count": new_count,
        "vacancies": records,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Выгрузка сохранена: %s → %s", count(len(records), *VACANCY), path)
    return path


def _cached_detail_ids() -> set[str]:
    """ID вакансий, полные описания которых уже есть в кэше data/details/."""
    return {path.stem for path in DETAILS_DIR.glob("*.json")}


def _all_vacancy_ids(areas: Optional[set[str]] = None) -> list[str]:
    """ID вакансий из всех файлов data/raw_vacancies_*.json, без дублей, в порядке появления.

    areas — названия регионов/городов (например, {"Минск"}), чтобы взять только их вакансии.
    """
    ids: dict[str, None] = {}
    for path in sorted(DATA_DIR.glob("raw_vacancies_*.json")):
        for vacancy in json.loads(path.read_text(encoding="utf-8"))["vacancies"]:
            if areas is None or vacancy.get("area") in areas:
                ids[str(vacancy["id"])] = None
    return list(ids)


def area_names(client: HHClient, region_ids: list[int]) -> set[str]:
    """Названия регионов HH и всех вложенных в них областей и городов (справочник /areas/{id}).

    В выгрузке у вакансии хранится только название города, поэтому, чтобы отобрать вакансии
    страны (например, Беларусь — 16), нужен полный список её населённых пунктов.
    """
    names: set[str] = set()

    def walk(node: dict[str, Any]) -> None:
        names.add(node["name"])
        for child in node.get("areas") or []:
            walk(child)

    for region_id in region_ids:
        walk(client.get(f"/areas/{region_id}"))
    return names


def _save_detail(vacancy_id: str, detail: dict[str, Any]) -> None:
    """Сохраняет описание в data/details/{id}.json через временный файл (без обрывков при Ctrl+C)."""
    path = DETAILS_DIR / f"{vacancy_id}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def fetch_details(limit: Optional[int] = None, region_ids: Optional[list[int]] = None,
                  areas: Optional[set[str]] = None) -> tuple[int, int]:
    """Догружает полные описания и key_skills для вакансий, которых нет в кэше (AC 2.7).

    Берутся вакансии из всех файлов выгрузок, поэтому то, что не загрузилось
    в прошлый раз, загружается при следующем запуске. Вакансии, удалённые с HH (404),
    сохраняются в кэш с description = None, чтобы не запрашивать их повторно.
    limit — ограничение количества (для проверки на небольшой выборке);
    region_ids — ID регионов HH (как --region): только вакансии этих регионов и вложенных городов;
    areas — названия городов напрямую (например, {"Минск"}).
    Возвращает (загружено, ошибок).
    """
    settings = load_preferences()["search_settings"]
    client = HHClient(delay=float(settings.get("request_delay_sec") or DEFAULT_DELAY))
    if region_ids:
        areas = (areas or set()) | area_names(client, region_ids)
        logger.info("Описания только для регионов %s: %s в справочнике HH", ", ".join(map(str, region_ids)),
                    count(len(areas), "населённый пункт", "населённых пункта", "населённых пунктов"))

    cached = _cached_detail_ids()
    pending = [vid for vid in _all_vacancy_ids(areas) if vid not in cached]
    if limit is not None:
        pending = pending[:limit]
    if not pending:
        logger.info("Все описания вакансий уже в кэше")
        return 0, 0

    DETAILS_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Загружаю полные описания: %s (%s)", count(len(pending), *VACANCY), duration(len(pending) * client.delay))

    loaded = failed = failed_in_row = 0
    started = time.monotonic()
    for n, vacancy_id in enumerate(pending, start=1):
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        try:
            data = client.get(f"/vacancies/{vacancy_id}")
            detail = {
                "id": vacancy_id,
                "description": _html_to_paragraphs(data.get("description")),
                "key_skills": [skill["name"] for skill in data.get("key_skills") or []],
                "fetched_at": now,
            }
        except HHApiError as exc:
            if exc.code != "404":
                failed += 1
                failed_in_row += 1
                logger.error("Вакансия %s: %s", vacancy_id, exc)
                if failed_in_row >= MAX_DETAIL_FAILURES:
                    logger.error("%d ошибок подряд — останавливаю загрузку описаний, "
                                 "оставшиеся загрузятся при следующем fetch", failed_in_row)
                    break
                continue
            logger.warning("Вакансия %s удалена с HH, сохраняю без описания", vacancy_id)
            detail = {"id": vacancy_id, "description": None, "key_skills": [], "fetched_at": now}

        _save_detail(vacancy_id, detail)
        loaded += 1
        failed_in_row = 0
        if n % 10 == 0 or n == len(pending):
            left_sec = (time.monotonic() - started) / n * (len(pending) - n)
            status = f"осталось {duration(left_sec)}" if n < len(pending) else "готово"
            logger.info("Описания: %d/%d (%s)", n, len(pending), status)

    logger.info("Описаний загружено: %d, ошибок: %d", loaded, failed)
    return loaded, failed
