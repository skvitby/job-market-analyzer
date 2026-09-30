---
title: Установка и первый запуск
tags: [user-guide, job-market-analyzer]
---

# Установка и первый запуск

[← к оглавлению](index.md)

## 1. Установка

Из корня проекта в PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Зависимости (`requirements.txt`): `typer` (CLI), `requests`, `python-dotenv` (чтение `.env`), `anthropic` (Claude API), `openai` (клиент для DeepSeek, Qwen и Ollama). Отдельно нужен `curl` — через него идут запросы к HH (см. раздел 3).

## 2. Файл `.env`

Скопируйте `.env.example` в `.env` в корне проекта и заполните. `.env` исключён из git (`.gitignore`) — ключи храните только в нём (NFR-2), не в `preferences.json` и не в коде.

| Переменная | Зачем | Где взять | Когда нужна |
|---|---|---|---|
| `HH_USER_AGENT` | Заголовок User-Agent для HH API: `AppName/1.0 (your_email@example.com)` | Придумать название приложения + свой email | Всегда для запросов к HH |
| `HH_CLIENT_ID`, `HH_CLIENT_SECRET` | Данные приложения HH — нужны, чтобы получить токен | Выдаются после одобрения заявки на [dev.hh.ru](https://dev.hh.ru) | Один раз, для получения `HH_APP_TOKEN`; сам код их не читает |
| `HH_APP_TOKEN` | Токен приложения — без него поиск HH возвращает 403 | `POST https://api.hh.ru/token` с `grant_type=client_credentials` по Client ID/Secret; токен бессрочный | Всегда для запросов к HH |
| `HH_CURL_PATH` | Путь к `curl`, исключённому из VPN | Путь к `curl.exe`, который вы исключили в настройках VPN | Если работаете через VPN. Пусто — берётся `curl` из PATH |
| `ANTHROPIC_API_KEY` | Ключ Claude API | [console.anthropic.com](https://console.anthropic.com), оплата — предоплатой, отдельно от подписки Claude | Если в `llm_providers` выбран `anthropic` (по умолчанию — обе задачи) |
| `DEEPSEEK_API_KEY` | Ключ DeepSeek | [platform.deepseek.com](https://platform.deepseek.com) — см. [подключение DeepSeek и Qwen](deepseek-qwen-setup.md) | Только если выбран `deepseek` |
| `DASHSCOPE_API_KEY` | Ключ Qwen (Alibaba Cloud Model Studio) | Консоль Model Studio, регион Singapore — см. [подключение DeepSeek и Qwen](deepseek-qwen-setup.md) | Только если выбран `qwen` |

Для локальной Ollama ключ не нужен.

> [!tip] Какие переменные нужны каждой команде
> - `fetch` — `HH_USER_AGENT`, `HH_APP_TOKEN`, при VPN — `HH_CURL_PATH`.
> - `analyze` — только ключ LLM-провайдера; с `--no-llm` — ничего (работает офлайн по локальным данным).
> - `cover-letter`, `cv-tips` — ключ провайдера `cv_processing`; переменные HH — если описания вакансии ещё нет в кэше `data/details/` (тогда она запрашивается у HH).

## 3. Сеть: HH вне VPN, LLM — через VPN

HH блокирует IP VPN-сервисов (защита DDoS-Guard), а Claude API и другие зарубежные сервисы из РФ/РБ доступны через VPN. Поэтому трафик разделён:

| Куда | Чем выполняется | Через VPN? |
|---|---|---|
| HH API (`fetch`, запрос вакансии в `cover-letter` / `cv-tips`) | Внешний `curl` (путь — `HH_CURL_PATH`) | **Нет** — `curl` исключён из VPN по приложению (split tunneling) |
| LLM-провайдеры (Anthropic, DeepSeek, Qwen) | Сам Python | Да |
| Ollama | Python → `localhost:11434` | Не выходит в интернет |

**Что сделать один раз:** в настройках VPN-клиента исключить из туннеля конкретный `curl.exe` и прописать путь к нему в `HH_CURL_PATH`. Python из VPN не исключайте — иначе перестанут работать запросы к Claude.

> [!warning] Признак того, что HH видит VPN
> Сообщение `HH заблокировал IP (DDoS-Guard): проверьте, что curl исключён из VPN` — `curl` из `HH_CURL_PATH` всё-таки идёт через VPN или путь указывает не на тот `curl`. Подробнее — [Частые проблемы](troubleshooting.md#HH%20и%20сеть).

## 4. Резюме и настройки

- `profile/preferences.json` — фильтры поиска, словарь навыков, LLM и параметры писем. Файл уже есть в репозитории; если его удалить, программа работает на значениях по умолчанию. Подробно — [Настройки](configuration.md).
- `profile/my_cv.md` — ваше резюме в Markdown. Нужен для `analyze` (раздел «Сравнение с резюме»), `cover-letter`, `cv-tips`. Не хранится в git.

## 5. Как запускать команды

Все команды запускаются **из корня проекта** как модуль: `python -m src.cli <команда> [опции]`.

**PowerShell** — без активации venv, напрямую интерпретатором из `.venv`:

```powershell
.venv\Scripts\python.exe -m src.cli fetch --region 16
.venv\Scripts\python.exe -m src.cli analyze --area Минск
.venv\Scripts\python.exe -m src.cli cover-letter 137587921
.venv\Scripts\python.exe -m src.cli cv-tips 137587921
```

С активированным venv (`.venv\Scripts\activate`) достаточно `python -m src.cli …`.

**Claude Code, через `!`** — команда выполняется в bash, поэтому слэши прямые:

```
! .venv/Scripts/python.exe -m src.cli cv-tips 137587921
```

**Справка** по любой команде — `--help`:

```powershell
.venv\Scripts\python.exe -m src.cli --help
.venv\Scripts\python.exe -m src.cli fetch --help
```

> [!note] Кодировка вывода
> При запуске не из окна консоли (через `!` в Claude Code, с перенаправлением вывода, по расписанию) Python на Windows по умолчанию пишет в cp1251. CLI сам переключает такой вывод на UTF-8, поэтому кириллица читается и команда не падает на символе «→». Отдельно ничего настраивать не нужно.

## 6. Проверка установки

1. `.venv\Scripts\python.exe -m src.cli --help` — выводится список команд `fetch`, `analyze`, `cover-letter`, `cv-tips`.
2. `.venv\Scripts\python.exe -m src.cli fetch --region 16 --no-details` — первая выгрузка по Беларуси без полных описаний (быстро). Ожидаемо: строки лога `Первая выгрузка для регионов 16: ищу вакансии за последние 14 дн.`, затем `Готово: N вакансий (новых N) → …\data\raw_vacancies_….json`.
3. `.venv\Scripts\python.exe -m src.cli analyze --no-llm` — отчёт по словарю без обращений к LLM, `Готово: в отчёте N вакансий → …\reports\market_skills_summary.md`.

Дальше — [типовой сценарий](workflow.md).
