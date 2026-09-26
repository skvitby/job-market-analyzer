"""Вспомогательные функции для текстов, которые видит пользователь."""


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
