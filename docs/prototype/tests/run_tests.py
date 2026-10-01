"""Сценарные прогоны кликабельного прототипа docs/prototype/index.html в headless Chrome.

Каждый *.js в этой папке — сценарий: он кликает по интерфейсу прототипа и записывает
фактические значения в атрибут data-r у <body>. Скрипт встраивает сценарий в копию
index.html, открывает её в Chrome без окна и печатает результат.

Прогон падает (код выхода 1), если сценарий не дошёл до конца, поймал исключение или
на странице была ошибка JavaScript. Ожидаемые значения в сценариях не зашиты —
их сверяют глазами по выводу (что должно получиться — в docs/prototype/README.md).

Запуск из корня проекта:
    python docs/prototype/tests/run_tests.py            # все сценарии
    python docs/prototype/tests/run_tests.py 05 07      # только сценарии с этими префиксами
Путь к Chrome можно задать переменной окружения CHROME.
"""

import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROTOTYPE = TESTS_DIR.parent / "index.html"
CHROME_CANDIDATES = [
    os.environ.get("CHROME", ""),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "google-chrome", "chromium", "chromium-browser",
]
TIME_BUDGET_MS = 150000  # виртуальное время страницы: сценарии ждут завершения имитаций команд


def find_chrome() -> str:
    for candidate in CHROME_CANDIDATES:
        if candidate and (Path(candidate).exists() or shutil.which(candidate)):
            return candidate
    sys.exit("Chrome не найден. Укажите путь к chrome.exe в переменной окружения CHROME.")


def run_scenario(chrome: str, scenario: Path, workdir: Path) -> tuple[bool, list[str]]:
    page = PROTOTYPE.read_text(encoding="utf-8")
    script = scenario.read_text(encoding="utf-8")
    test_page = workdir / f"{scenario.stem}.html"
    test_page.write_text(page.replace("</body>", f"<script>\n{script}\n</script>\n</body>"), encoding="utf-8")
    dom = subprocess.run(
        [chrome, "--headless=new", "--disable-gpu", f"--virtual-time-budget={TIME_BUDGET_MS}",
         "--dump-dom", test_page.as_uri()],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
    ).stdout
    result = re.search(r'data-r="([^"]*)"', dom)
    page_errors = re.search(r'data-err="([^"]*)"', dom)
    title = re.search(r"<title>([^<]*)</title>", dom)
    lines = html.unescape(result.group(1)).split(" || ") if result else ["нет результата: сценарий не дошёл до конца"]
    failed = (not result
              or any(x.startswith("EXC") for x in lines)
              or any(x.startswith("errors=") and x != "errors=none" for x in lines)
              or bool(page_errors)
              or bool(title and title.group(1).startswith("ERR")))
    if page_errors:
        lines.append("ошибки страницы: " + html.unescape(page_errors.group(1)))
    return not failed, lines


def main() -> None:
    prefixes = sys.argv[1:]
    scenarios = sorted(p for p in TESTS_DIR.glob("*.js") if not prefixes or p.name.startswith(tuple(prefixes)))
    if not scenarios:
        sys.exit("Сценарии не найдены.")
    chrome = find_chrome()
    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        for scenario in scenarios:
            ok, lines = run_scenario(chrome, scenario, Path(tmp))
            failed += not ok
            print(f"\n{'OK  ' if ok else 'FAIL'} {scenario.name}")
            for line in lines:
                print("     " + line.replace("\n", " ")[:220])
    print(f"\nСценариев: {len(scenarios)}, с ошибками: {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
