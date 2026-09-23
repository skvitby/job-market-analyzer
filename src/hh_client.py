"""Интеграция с API HH.ru (US-02).

Запросы к HH выполняются через внешний curl (путь задаётся в HH_CURL_PATH),
а не через requests: так трафик к HH можно исключить из VPN по приложению,
оставив остальной Python (в том числе Claude API) внутри VPN.
"""

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

logger = logging.getLogger(__name__)

API_URL = "https://api.hh.ru"
DATA_DIR = PROJECT_ROOT / "data"

PER_PAGE = 100            # максимум HH API
MAX_RESULTS = 2000        # HH отдаёт не больше 2000 вакансий на один поиск
MAX_RETRIES = 3           # повторы при 429/503 и сетевых сбоях
RETRY_BASE_DELAY = 10.0   # пауза перед повтором: 10, 20, 30 сек
DEFAULT_DELAY = 3.0       # пауза между запросами (NFR-1)
MAX_PERIOD_DAYS = 30      # HH ищет вакансии не старше 30 дней
FETCH_OVERLAP = timedelta(hours=1)  # запас при догрузке, дубли убирает дедупликация

# Соответствие значений из preferences.json параметрам HH API (AC 2.5).
WORK_FORMAT_MAP = {"office": "ON_SITE", "hybrid": "HYBRID", "remote": "REMOTE"}
EMPLOYMENT_MAP = {"full": "FULL", "part": "PART", "project": "PROJECT"}
EXPERIENCE_VALUES = {"noExperience", "between1And3", "between3And6", "moreThan6"}


class HHApiError(RuntimeError):
    """Ошибка обращения к HH API с понятным пользователю описанием."""


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


def last_fetch_time() -> Optional[datetime]:
    """Время последней выгрузки по самому свежему файлу в data/ или None, если выгрузок не было."""
    files = sorted(DATA_DIR.glob("raw_vacancies_*.json"))
    if not files:
        return None
    try:
        fetched_at = json.loads(files[-1].read_text(encoding="utf-8"))["fetched_at"]
        return datetime.fromisoformat(fetched_at).astimezone()
    except (ValueError, KeyError) as exc:
        logger.warning("Не удалось прочитать fetched_at из %s (%s), беру время изменения файла", files[-1], exc)
        return datetime.fromtimestamp(files[-1].stat().st_mtime).astimezone()


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
            raise HHApiError(self._describe_error(code, body, result.returncode))

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
    logger.warning("Найдено %d вакансий, HH отдаёт только первые %d. Сузьте фильтры.", found, MAX_RESULTS)
    return items


_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: Optional[str]) -> Optional[str]:
    """Убирает из сниппета HTML-теги подсветки (<highlighttext>)."""
    return _TAG_RE.sub("", text) if text else text


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
    last_fetch = last_fetch_time()
    if last_fetch is None:
        # Первый запуск (AC 2.6): вакансии за initial_period_days дней.
        period_days, date_from = int(settings.get("initial_period_days") or 14), None
        logger.info("Первый запуск: ищу вакансии за последние %d дн.", period_days)
    else:
        # Повторный запуск: с момента прошлой выгрузки, но не глубже, чем позволяет HH.
        period_days = None
        date_from = max(last_fetch - FETCH_OVERLAP, now - timedelta(days=MAX_PERIOD_DAYS))
        logger.info("Ищу вакансии с %s (прошлая выгрузка %s)", f"{date_from:%d.%m %H:%M}", f"{last_fetch:%d.%m %H:%M}")
    params = build_search_params(settings, period_days, date_from)
    client = HHClient(delay=float(settings.get("request_delay_sec") or DEFAULT_DELAY))

    items = _collect(client, params, split_keys=["area", "experience"])
    records = list({item["id"]: to_record(item) for item in items}.values())  # без дублей

    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / f"raw_vacancies_{now:%Y%m%d_%H%M%S}.json"
    payload = {
        "fetched_at": now.isoformat(timespec="seconds"),
        "period_days": period_days,
        "date_from": date_from.isoformat(timespec="seconds") if date_from else None,
        "search_params": [[k, v] for k, v in params],
        "count": len(records),
        "vacancies": records,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Сохранено %d вакансий в %s", len(records), path)
    return path
