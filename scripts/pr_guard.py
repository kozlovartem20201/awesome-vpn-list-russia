"""Проверка pull request'ов, открытых напрямую, а не через форму заявки.

Запускается из .github/workflows/pr-guard.yml на `pull_request_target`. Никогда
не делает checkout и не выполняет код из PR: метаданные и README.md читаются
через API как данные.

Что делает:
  - новые сервисы от авторов без принятых PR закрывает и отправляет в форму заявки;
  - аккаунты моложе порога и из блок-листа закрывает;
  - проверяет новые ссылки (https, без рефералок, UTM и сокращателей);
  - помечает смену домена у существующего сервиса (domain-change) — так угоняют ссылки;
  - помечает правки автоматизации и правил (sensitive-change).

Окружение: GITHUB_EVENT_PATH, GITHUB_REPOSITORY, GH_TOKEN.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
import unicodedata
from pathlib import Path

import readme
from github_api import GitHub, GitHubError, neutralize
from netcheck import REPO_URL
from policy import identity_key, link_targets, url_problems
from trust import assess

MARKER = "<!-- awesome-vpn-list-russia-bot:pr-guard -->"
POLICY = readme.ROOT / ".github" / "submission-policy.toml"
SENSITIVE_PREFIXES = (".github/", "scripts/", "tests/", "docs/")
SENSITIVE_FILES = {"SECURITY.md", "CONTRIBUTING.md", ".gitattributes"}
MAX_README_BYTES = 500_000
VENDOR_RE = re.compile(r"^- \[[xX]\] Я владелец или сотрудник", re.M)


def key(name: str) -> str:
    return unicodedata.normalize("NFKC", name).casefold().strip()


def readme_changes(base: str, head: str) -> dict:
    """Новые сервисы, смена доменов и новые ссылки между двумя версиями README."""
    before = {key(s.name): s for s in readme.services(base)}
    after = {key(s.name): s for s in readme.services(head)}
    added = [after[k].name for k in after if k not in before]
    moved = [(after[k].name, identity_key(before[k].url), identity_key(after[k].url))
             for k in after if k in before and identity_key(before[k].url) != identity_key(after[k].url)]
    old_links = set(link_targets(base))
    new_links = sorted({t for t in link_targets(head) if "://" in t} - old_links)
    known = {identity_key(t) for t in old_links if t.startswith("https://")}
    new_domains = sorted({identity_key(t) for t in new_links if t.startswith("https://")} - known)
    return {"added": added, "moved": moved, "new_links": new_links, "new_domains": new_domains}


def run(event: dict, gh: GitHub, *, dry_run: bool) -> int:
    pr = event["pull_request"]
    number, author = pr["number"], pr["user"]["login"]
    if pr["user"].get("type") == "Bot":
        print("PR открыт ботом; пропускаю.")
        return 0
    if pr.get("state") != "open":
        return 0

    files = gh.pull_files(number)
    paths = [f["filename"] for f in files]
    changes = {"added": [], "moved": [], "new_links": [], "new_domains": []}
    readme_broken = False
    if "README.md" in paths:
        head_repo = ((pr.get("head") or {}).get("repo") or {}).get("full_name", gh.repo)
        base_text = gh.file_at(gh.repo, "README.md", pr["base"]["sha"]) or ""
        head_text = gh.file_at(head_repo, "README.md", pr["head"]["sha"])
        if head_text is None or len(head_text.encode()) > MAX_README_BYTES:
            readme_broken = True
        else:
            changes = readme_changes(base_text.replace("\r\n", "\n"), head_text.replace("\r\n", "\n"))

    with open(POLICY, "rb") as f:
        policy = tomllib.load(f)
    kind = "new_service" if changes["added"] else "update"
    trust = assess(gh, policy, author, pr.get("author_association", "NONE"), kind=kind, check_limits=False)
    if trust.maintainer:
        print("PR мейнтейнера; пропускаю.")
        return 0

    problems: list[str] = []
    notes: list[str] = []
    labels: set[str] = set()
    close = False

    if not trust.ok:
        problems += trust.errors
        labels.add("trust-check-failed")
        close = True
    elif changes["added"] and not trust.trusted:
        names = ", ".join(f"«{neutralize(n)}»" for n in changes["added"])
        problems.append(f"Новые сервисы ({names}) от авторов без принятых PR добавляются только через "
                        f"[форму заявки]({REPO_URL}/issues/new?template=add-vpn.yml). Форма проверяет то же самое "
                        f"и сама откроет pull request.")
        labels.add("use-submission-form")
        close = True

    if readme_broken:
        problems.append("Не удалось прочитать README.md из этого PR.")
    for url in changes["new_links"]:
        for problem in url_problems(url):
            problems.append(f"Ссылка {neutralize(url)} {problem}.")
            labels.add("needs-changes")
    for name, before, after in changes["moved"]:
        labels.add("domain-change")
        notes.append(f"У «{neutralize(name)}» меняется сайт: `{neutralize(before)}` → `{neutralize(after)}`. "
                     f"Нужно подтверждение, что новый домен принадлежит тому же сервису: старый сайт "
                     f"перенаправляет на новый или есть официальное объявление.")
    if changes["new_domains"]:
        notes.append("Новые домены в ссылках: " + ", ".join(f"`{neutralize(d)}`" for d in changes["new_domains"][:15])
                     + ". Проверьте, что это официальные сайты, а не копии.")

    sensitive = [p for p in paths if p.startswith(SENSITIVE_PREFIXES) or p in SENSITIVE_FILES]
    if sensitive:
        labels.add("sensitive-change")
        notes.append("PR меняет автоматизацию или правила ("
                     + ", ".join(f"`{neutralize(p)}`" for p in sensitive[:10])
                     + ") — нужно внимательное ревью мейнтейнера.")
    if VENDOR_RE.search(pr.get("body") or ""):
        labels.add("vendor-submitted")
    if trust.flags:
        labels.add("needs-manual-review")
        notes += [f"Автор: {f}" for f in trust.flags]

    print(json.dumps({"problems": problems, "notes": notes, "labels": sorted(labels), "close": close},
                     ensure_ascii=False, indent=2))
    if dry_run:
        return 0
    if labels:
        gh.add_labels(number, sorted(labels))
    if problems or notes:
        body = []
        if problems:
            body += ["### Проблемы", *[f"- {p}" for p in problems], ""]
        if notes:
            body += ["### Для ревьюера", *[f"- {n}" for n in notes], ""]
        if close:
            body.append("PR закрыт автоматически. Если это ошибка, напишите здесь — мейнтейнер посмотрит.")
        gh.upsert_comment(number, MARKER, "\n".join(body))
    if close:
        gh.set_state(number, "closed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_PATH"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.event:
        parser.error("нужен --event или GITHUB_EVENT_PATH")
    event = json.loads(Path(args.event).read_text(encoding="utf-8"))
    gh = GitHub(os.environ.get("GITHUB_REPOSITORY", "kozlovartem20201/awesome-vpn-list-russia"))
    try:
        return run(event, gh, dry_run=args.dry_run)
    except GitHubError as exc:
        print(f"Ошибка GitHub API: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
