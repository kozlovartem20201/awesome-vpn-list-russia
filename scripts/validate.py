"""Проверка README.md для CI.

Использование:
    python scripts/validate.py                          # структура, ссылки, якоря
    python scripts/validate.py --base origin/main       # плюс список новых ссылок относительно base
    python scripts/validate.py --base origin/main --online  # и проверка, что новые ссылки открываются

Ошибки дают код выхода 1, предупреждения только печатаются. В GitHub Actions
находки выводятся как аннотации к строкам README.md.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import unicodedata

import readme
from netcheck import REPO_URL, fetch
from policy import HIDDEN_CHARS_RE, identity_key, link_targets, url_problems

IN_CI = os.environ.get("GITHUB_ACTIONS") == "true"


class Report:
    def __init__(self) -> None:
        self.errors = 0
        self.warnings = 0

    def emit(self, level: str, message: str, line: int | None = None) -> None:
        if level == "error":
            self.errors += 1
        else:
            self.warnings += 1
        if IN_CI:
            location = f" file=README.md,line={line}" if line else " file=README.md"
            print(f"::{level}{location}::{message}")
        else:
            print(f"{level.upper():7} README.md{f':{line}' if line else ''}: {message}")


def line_of(text: str, needle: str) -> int | None:
    pos = text.find(needle)
    return text.count("\n", 0, pos) + 1 if pos >= 0 else None


def check_structure(text: str, report: Report) -> None:
    lines = text.split("\n")
    try:
        start = readme.marker_index(lines, readme.SERVICES_START)
        end = readme.marker_index(lines, readme.SERVICES_END)
        reviews_end = readme.marker_index(lines, readme.REVIEWS_END)
    except ValueError as exc:
        report.emit("error", str(exc))
        return
    if not start < end < reviews_end:
        report.emit("error", "комментарии services:start, services:end и reviews:end должны идти в этом порядке")
        return

    table = [(i, line) for i, line in enumerate(lines[start + 1:end], start + 1) if line.strip()]
    if len(table) < 2 or table[0][1].strip() != readme.TABLE_HEADER:
        report.emit("error", f"сразу после services:start должна идти шапка таблицы `{readme.TABLE_HEADER}`", start + 1)
        return
    for i, line in table[2:]:
        if not readme.ROW_RE.match(line):
            report.emit("error", "строка таблицы должна начинаться с `| **[Название](https://…)** |`", i + 1)
        elif len(readme.split_row(line)) != 4:
            report.emit("error", f"в строке таблицы должно быть 4 ячейки, найдено {len(readme.split_row(line))}", i + 1)

    try:
        reviews = readme.review_headings(text)
    except ValueError as exc:
        report.emit("error", str(exc))
        return
    for svc in readme.services(text):
        if svc.in_main_table and not any(h == svc.name or h.startswith(svc.name + " ") for h in reviews):
            report.emit("error", f"у сервиса «{svc.name}» нет обзора: заголовок `### {svc.name}` "
                                 f"в разделе «{readme.REVIEWS_HEADING}»", svc.line)


def check_duplicates(text: str, report: Report) -> None:
    names: dict[str, int] = {}
    keys: dict[str, int] = {}
    for svc in readme.services(text):
        name = unicodedata.normalize("NFKC", svc.name).casefold()
        if name in names:
            report.emit("error", f"«{svc.name}» уже есть в строке {names[name]}", svc.line)
        names.setdefault(name, svc.line)
        key = identity_key(svc.url)
        if key in keys:
            report.emit("error", f"сайт `{key}` уже указан в строке {keys[key]}", svc.line)
        keys.setdefault(key, svc.line)


def check_links(text: str, report: Report) -> None:
    anchors = {a for _, _, a in readme.headings(text)}
    for target in sorted(set(link_targets(text))):
        line = line_of(text, f"]({target})")
        if target.startswith("#"):
            if target[1:] not in anchors:
                report.emit("error", f"ссылка `{target}` никуда не ведёт: такого заголовка нет", line)
        elif target.startswith(("http://", "https://")):
            for problem in url_problems(target):
                report.emit("error", f"ссылка {target} {problem}", line)
        elif "://" in target or target.startswith(("mailto:", "javascript:", "data:")):
            report.emit("error", f"недопустимая ссылка `{target}`", line)
        elif not (readme.ROOT / target.split("#")[0]).exists():
            report.emit("error", f"файл `{target}` не найден в репозитории", line)
    for i, line in enumerate(text.split("\n"), 1):
        if HIDDEN_CHARS_RE.search(line):
            report.emit("error", "строка содержит невидимые Unicode-символы", i)


def base_text(ref: str) -> str:
    result = subprocess.run(["git", "show", f"{ref}:README.md"], cwd=readme.ROOT,
                            capture_output=True, encoding="utf-8", check=True)
    return result.stdout.replace("\r\n", "\n")


def check_new_links(text: str, base: str, report: Report, *, online: bool) -> None:
    old = set(link_targets(base))
    new = sorted({t for t in link_targets(text) if t.startswith("https://")} - old)
    known = {identity_key(t) for t in old if t.startswith("https://")} | {identity_key(REPO_URL)}
    for target in new:
        line = line_of(text, f"]({target})")
        if identity_key(target) not in known:
            known.add(identity_key(target))
            report.emit("warning", f"новый домен в ссылках: `{identity_key(target)}` — проверьте, что он официальный", line)
        if not online:
            continue
        result = fetch(target)
        if result.broken:
            report.emit("error", f"ссылка {target} не открывается ({result.describe()})", line)
        elif result.unverifiable:
            report.emit("warning", f"ссылка {target} ответила {result.describe()} — проверьте вручную", line)
        if result.final_url and result.moved_domain:
            report.emit("warning", f"ссылка {target} перенаправляет на другой домен: {result.final_url}", line)
    print(f"Новых ссылок относительно base: {len(new)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", help="git-ref, с которым сравнивать README (например, SHA базы PR)")
    parser.add_argument("--online", action="store_true", help="проверить, что новые ссылки открываются")
    args = parser.parse_args()

    text = readme.read()
    report = Report()
    check_structure(text, report)
    check_duplicates(text, report)
    check_links(text, report)
    if args.base:
        check_new_links(text, base_text(args.base), report, online=args.online)
    print(f"Ошибок: {report.errors}, предупреждений: {report.warnings}")
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
