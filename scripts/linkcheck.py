"""Еженедельная проверка всех ссылок README.md.

Сообщает о битых ссылках и о сайтах сервисов, которые стали перенаправлять на
другой домен: так выглядит истёкший, проданный или угнанный домен — и VPN,
который тихо закрылся.

Отчёт печатается в stdout. С --issue он живёт в одном issue с меткой
link-report: обновляется при каждом запуске и закрывается, когда всё чисто.

Окружение для --issue: GITHUB_REPOSITORY, GH_TOKEN.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import readme
from github_api import GitHub, neutralize
from netcheck import fetch
from policy import link_targets

LABEL = "link-report"
TITLE = "Отчёт о проверке ссылок"
SKIP_HOSTS = ("https://img.shields.io/", "https://awesome.re/", "https://github.com/kozlovartem20201/awesome-vpn-list-russia")


def build_report(text: str) -> tuple[str, bool]:
    services = {s.url: s.name for s in readme.services(text)}
    links = sorted({t for t in link_targets(text) if t.startswith("https://") and not t.startswith(SKIP_HOSTS)})
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = dict(zip(links, pool.map(fetch, links)))

    broken, moved, unverifiable = [], [], []
    for url, result in results.items():
        name = services.get(url, "")
        label = f"{neutralize(name)} — сайт" if name else "ссылка"
        if result.broken:
            broken.append(f"| {label} | {url} | {neutralize(result.describe())} |")
        elif result.unverifiable:
            unverifiable.append(f"- {url} ({neutralize(result.describe())})")
        if name and result.final_url and result.moved_domain:
            moved.append(f"| {neutralize(name)} | {url} | {neutralize(result.final_url)} |")

    lines = [f"Проверено ссылок: {len(results)}, дата: {dt.date.today().isoformat()}.", ""]
    if moved:
        lines += [
            "## Сайт сервиса перенаправляет на другой домен",
            "",
            "Убедитесь, что переезд настоящий (ребрендинг), прежде чем менять ссылку. Неожиданный редирект "
            "может означать, что домен продан, истёк или угнан, — если сомневаетесь, сначала уберите ссылку.",
            "",
            "| Сервис | Ссылка в README | Теперь ведёт на |",
            "| --- | --- | --- |",
            *moved,
            "",
        ]
    if broken:
        lines += ["## Битые ссылки", "", "Сервис мог закрыться — проверьте и уберите его из списка.", "",
                  "| Что | Ссылка | Результат |", "| --- | --- | --- |", *broken, ""]
    if unverifiable:
        lines += [f"<details><summary>Не удалось проверить автоматически (защита от ботов, лимиты): "
                  f"{len(unverifiable)}</summary>", "", *unverifiable, "", "</details>"]
    if not moved and not broken:
        lines.append("Битых ссылок и неожиданных редиректов нет.")
    return "\n".join(lines), bool(moved or broken)


def upsert_issue(gh: GitHub, body: str, findings: bool) -> None:
    _, items = gh.search_count(f"repo:{gh.repo} is:issue is:open label:{LABEL}")
    existing = items[0]["number"] if items else None
    if findings:
        if existing:
            gh.update_issue_body(existing, body)
        else:
            gh.create_issue(TITLE, body, [LABEL])
    elif existing:
        gh.update_issue_body(existing, body)
        gh.set_state(existing, "closed", "completed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--issue", action="store_true", help="вести отчёт в issue с меткой link-report")
    args = parser.parse_args()
    body, findings = build_report(readme.read())
    print(body)
    if args.issue:
        upsert_issue(GitHub(os.environ.get("GITHUB_REPOSITORY", "kozlovartem20201/awesome-vpn-list-russia")),
                     body, findings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
