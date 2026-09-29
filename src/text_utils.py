"""Вспомогательные функции для текстов: формы слов для пользователя и сверка цитат LLM с исходным текстом."""

import re


def plural(n: int, one: str, few: str, many: str) -> str:
    """Форма слова, согласованная с числом (русское правило).

    one — для 1, 21, 101 («вакансия»); few — для 2–4, 22–24 («вакансии»);
    many — для 0, 5–20, 25–30, 11–14 («вакансий»).
    Для других падежей передаются соответствующие формы, например после «из»:
    plural(n, "вакансии", "вакансий", "вакансий").
    """
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def count(n: int, one: str, few: str, many: str) -> str:
    """Число вместе с согласованной формой слова: count(22, "вакансия", "вакансии", "вакансий") -> "22 вакансии"."""
    return f"{n} {plural(n, one, few, many)}"


def duration(seconds: float) -> str:
    """Примерная длительность для оценок времени: «меньше минуты», «~3 минуты», «~1 ч 15 мин»."""
    if seconds < 60:
        return "меньше минуты"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"~{count(minutes, 'минута', 'минуты', 'минут')}"
    hours, rest = divmod(minutes, 60)
    return f"~{hours} ч {rest} мин" if rest else f"~{hours} ч"


VACANCY = ("вакансия", "вакансии", "вакансий")
SKILL = ("навык", "навыка", "навыков")


_QUOTE_SPLIT_RE = re.compile(r"[;,/()«»\"…]|\.\.\.")


def normalize_text(text: str) -> str:
    """Текст для сверки цитат: без markdown-разметки, в нижнем регистре, с одиночными пробелами."""
    return re.sub(r"\s+", " ", re.sub(r"[*_#`>]", "", text)).strip().lower()


def quote_found(quote: str, text_norm: str) -> bool:
    """Цитата подтверждается, если каждый её фрагмент (между ; , / … и т.п.) есть в тексте (AC 3.3, AC 4.6).

    text_norm — текст после normalize_text(). Модель на длинных списках склеивает цитату
    из несмежных пунктов («BPMN; Моделирование бизнес-процессов»), поэтому дословного
    совпадения всей цитаты не требуем.
    """
    fragments = [f.strip() for f in _QUOTE_SPLIT_RE.split(normalize_text(quote))]
    fragments = [f for f in fragments if len(f) >= 2]
    return bool(fragments) and all(f in text_norm for f in fragments)
