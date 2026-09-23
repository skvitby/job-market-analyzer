# Job Market Analyzer & Career Assistant

Учебный проект для портфолио бизнес/системного аналитика: инструмент для автоматизации поиска вакансий, анализа рынка труда и подготовки откликов.

**Статус:** 🚧 в разработке — готовы профиль настроек (US-01) и сбор вакансий с HH.ru (US-02).

## Идея проекта

Сократить время на поиск релевантных вакансий и подготовку качественных откликов (CV / Cover Letter) для позиции Бизнес- / Системный аналитик в РФ и РБ, а также продемонстрировать практический кейс автоматизации в портфолио.

## Основные задачи

1. Автоматизировать сбор и первичный фильтр вакансий с платформы HeadHunter.
2. Провести агрегированный анализ ключевых навыков и технологий (Gap Analysis) на основе требований рынка.
3. Автоматизировать генерацию персонализированных сопроводительных писем и рекомендаций по адаптации резюме под конкретные вакансии.

## Стек

Python 3.11+, HeadHunter API v1, Claude API (Anthropic SDK), Click/Typer (CLI).

## Как это работает

Все настройки (фильтры поиска, словарь навыков, тон и акценты писем) хранятся в одном файле `profile/preferences.json`. CLI-команды берут значения из него по умолчанию; явно переданные опции их переопределяют.

| Команда | Что делает | Результат |
|---|---|---|
| `fetch` | Выгружает вакансии с HH.ru по фильтрам из профиля (US-02) | `data/raw_vacancies_{timestamp}.json` |
| `analyze` | Gap Analysis: частота навыков на рынке и сравнение с резюме (US-03) | `reports/market_skills_summary.md` |
| `cover-letter <vacancy_id>` | Сопроводительное письмо под вакансию через Claude API (US-04) | `reports/cover_letters/cl_{vacancy_id}.md` |
| `cv-tips <vacancy_id>` | 3–5 советов по адаптации резюме под вакансию (US-05) | `reports/cv_tips_{vacancy_id}.md` |

Типовой сценарий:

```
python -m src.cli fetch
python -m src.cli analyze
python -m src.cli cover-letter 123456789
python -m src.cli cv-tips 123456789
```

Для `analyze`, `cover-letter` и `cv-tips` нужен актуальный файл резюме `profile/my_cv.md`.

## Как запустить

1. Установить зависимости:
   ```
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```
2. Скопировать `.env.example` в `.env` и заполнить:
   - `HH_USER_AGENT` — название приложения и контактный email;
   - `HH_CLIENT_ID`, `HH_CLIENT_SECRET` — выдаются после регистрации приложения на [dev.hh.ru](https://dev.hh.ru);
   - `HH_APP_TOKEN` — токен приложения (`POST https://api.hh.ru/token`, `grant_type=client_credentials`);
   - `HH_CURL_PATH` — путь к `curl`, если он исключён из VPN (HH блокирует IP VPN-сервисов);
   - `ANTHROPIC_API_KEY` — для команд, использующих Claude API.
3. Настроить фильтры в `profile/preferences.json` и запустить сбор:
   ```
   python -m src.cli fetch
   python -m src.cli fetch --region 16 --schedule remote   # разово переопределить профиль
   ```
   Первый запуск выгружает вакансии за 14 дней, последующие — с момента предыдущей выгрузки.

## Планируемая структура проекта

```
job-market-analyzer/
├── data/                  # Локальные данные вакансий (JSON/CSV)
├── profile/               # Профиль пользователя (my_cv.md, preferences.json)
├── reports/               # Сгенерированные отчеты и сопроводительные письма
│   └── cover_letters/
├── src/                   # Исходный код
│   ├── config.py          # Загрузка profile/preferences.json и значения по умолчанию
│   ├── hh_client.py       # Интеграция с API HH.ru
│   ├── analyzer.py        # Модуль парсинга навыков и Gap-анализа
│   ├── llm_service.py     # Интеграция с Claude API
│   └── cli.py             # Интерфейс командной строки
├── .env.example           # Шаблон конфига с переменными окружения
├── requirements.md        # Техническое задание (полное описание требований)
├── requirements.txt       # Зависимости Python
└── README.md              # Этот файл
```

Полное описание требований, пользовательских историй и Acceptance Criteria — в [`requirements.md`](./requirements.md).

## Roadmap

- [x] US-01: Конфигурационный профиль `profile/preferences.json`
- [x] US-02: Сбор вакансий с HH.ru
- [ ] US-03: Анализ рынка и Gap Analysis
- [ ] US-04: Генерация сопроводительных писем через Claude API
- [ ] US-05: Рекомендации по адаптации резюме

Открытые вопросы (накопление данных, отказоустойчивость и стоимость LLM-вызовов, полнота текста для Gap-анализа) описаны в разделе 8 `requirements.md`.

Этот README будет обновляться по мере разработки.
