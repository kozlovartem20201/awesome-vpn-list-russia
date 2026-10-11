#!/usr/bin/env python3
"""Бот заявок: issue-форма «Добавить VPN в список» → проверки → pull request.

Запускается из .github/workflows/submission.yml на событиях issues. Текст
issue — недоверенные данные: он разбирается здесь, в Python, и никогда не
попадает в shell.

Порядок:
  1. Проверка автора: возраст аккаунта, лимиты, блок-лист (.github/submission-policy.toml).
  2. Офлайн-проверка формы: ссылки, тексты, протоколы, дубликаты и похожие названия.
  3. Сетевые проверки: сайт, цены и политика открываются, нет редиректа на чужой
     домен, домену не меньше N дней, репозиторий клиента жив.
  4. Строка таблицы, обзор и пункт оглавления дописываются в README.md в ветке
     submission/issue-<N>, открывается (или обновляется) pull request.
     Мерджит всегда человек.

Окружение: GITHUB_EVENT_PATH, GITHUB_REPOSITORY, GH_TOKEN.
Локальный прогон без записи в GitHub:
    python scripts/submission.py --event event.json --dry-run      # с сетевыми проверками
    python scripts/submission.py --event event.json --offline      # без сети вообще
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import tomllib
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import readme
from github_api import GitHub, GitHubError, neutralize
from issue_forms import parse
from netcheck import REPO_URL, domain_registered, fetch, repo_health
from policy import identity_key, link_targets, registrable_domain, style_problems, text_problems, url_problems
from trust import TrustResult, assess, label_applied_by_maintainer

TEMPLATE = readme.ROOT / ".github" / "ISSUE_TEMPLATE" / "add-vpn.yml"
POLICY = readme.ROOT / ".github" / "submission-policy.toml"
MARKER = "<!-- awesome-vpn-list-russia-bot:submission -->"
SUBMISSION_LABEL = "submission"
OVERRIDE_LABEL = "maintainer-approved"
FORM_URL = f"{REPO_URL}/issues/new?template=add-vpn.yml"

# Вариант формы → как он выглядит в обзоре. Тест сверяет ключи с шаблоном.
PROTOCOLS = {
    "VLESS Reality (XRay)": "VLESS Reality",
    "VLESS, VMess или Trojan (XRay, V2Ray)": "VLESS/VMess/Trojan",
    "Shadowsocks / Outline": "Shadowsocks",
    "Hysteria2 или TUIC": "Hysteria2/TUIC",
    "AmneziaWG": "AmneziaWG",
    "OpenVPN с обфускацией (Cloak, stunnel и т. п.)": "OpenVPN с обфускацией",
    "Собственный протокол с обфускацией": "собственный протокол с обфускацией",
    "WireGuard без обфускации": "WireGuard",
    "OpenVPN без обфускации": "OpenVPN",
    "IKEv2 / IPSec": "IKEv2/IPSec",
}
OBFUSCATED = set(list(PROTOCOLS)[:7])
PLATFORMS = {
    "Android": "Android", "iOS": "iOS", "Windows": "Windows", "macOS": "macOS", "Linux": "Linux",
    "Android TV": "Android TV", "Роутеры": "роутеры",
    "Ключ или подписка для сторонних клиентов": "сторонние клиенты по ключу или подписке",
}
PAYMENT = {
    "Российские карты (МИР) или СБП": "российские карты (МИР), СБП",
    "Иностранные карты": "иностранные карты",
    "Криптовалюта": "криптовалюта",
    "Telegram Stars": "Telegram Stars",
    "Сервис полностью бесплатный": "не нужна — сервис бесплатный",
}
AFFILIATION = {
    "Не связан(а) с сервисом": "none",
    "Я владелец или сотрудник сервиса, либо он попросил меня": "vendor",
    "Другое (поясните ниже)": "other",
}

NAME_RE = re.compile(r"^[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё .+&'’-]{0,39}$")
TELEGRAM_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
REPO_RE = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
PRICE_RE = re.compile(
    r"^(бесплатно|(?:[$€]\s?\d{1,6}(?:[.,]\d{1,2})?|\d{1,6}(?:[.,]\d{1,2})?\s?(?:₽|\$|€))"
    r"(?:\s?/\s?\d{1,2}\s?мес)?)$", re.IGNORECASE)
LIMITS = {"summary": 200, "about": 700, "free": 80, "notes": 3000}


@dataclass
class Outcome:
    service: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    labels: set[str] = field(default_factory=set)
    facts: dict[str, object] = field(default_factory=dict)


def load_policy() -> dict:
    with open(POLICY, "rb") as f:
        return tomllib.load(f)


def one_line(text: object) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def name_key(name: str) -> str:
    """«Blanc VPN», «BlancVPN» и «blanc-vpn» — одно и то же."""
    text = unicodedata.normalize("NFKC", name).casefold()
    return re.sub(r"[^0-9a-zа-яё]", "", text).removesuffix("vpn") or text


def mixed_scripts(name: str) -> bool:
    """Латиница и кириллица в одном слове — типичный приём подделки названия."""
    for word in re.findall(r"\w+", name):
        if re.search(r"[A-Za-z]", word) and re.search(r"[А-Яа-яЁё]", word):
            return True
    return False


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def blocked_domain(url: str, policy: dict) -> bool:
    blocked = {d.lower() for d in policy.get("blocked_domains") or []}
    host = (urlsplit(url).hostname or "").lower()
    labels = host.split(".")
    return any(".".join(labels[i:]) in blocked for i in range(len(labels) - 1))


def checked(form: dict, field_id: str, mapping: dict[str, str]) -> list[str]:
    return [mapping[o] for o, on in (form.get(field_id) or {}).items() if on and o in mapping]


# ---------------------------------------------------------------- офлайн-проверки

def check_url(label: str, url: str, out: Outcome, policy: dict, *, required: bool) -> bool:
    if not url:
        if required:
            out.errors.append(f"**{label}**: укажите ссылку.")
        return False
    problems = url_problems(url)
    out.errors += [f"**{label}**: ссылка {p}." for p in problems]
    if not problems and blocked_domain(url, policy):
        out.errors.append(f"**{label}**: этот домен нельзя добавлять в список.")
        return False
    return not problems


def check_text(label: str, value: str, out: Outcome, *, limit: int, style: bool = True) -> None:
    problems = text_problems(value, max_length=limit)
    if not problems and style:
        problems = style_problems(value)
    out.errors += [f"**{label}**: {p}." for p in problems]


def check_price(label: str, value: str, out: Outcome, *, required: bool) -> str:
    value = re.sub(r"(\d)\s+₽", r"\1₽", one_line(value))
    if not value:
        if required:
            out.errors.append(f"**{label}**: укажите цену.")
        return "—"
    if not PRICE_RE.match(value):
        out.errors.append(f"**{label}**: не понимаю `{neutralize(value)}`. Пишите как `200₽`, `$5`, `€7.99`, "
                          f"`2390₽ / 6 мес` или `Бесплатно`.")
        return value
    return "Бесплатно" if value.lower() == "бесплатно" else value


def check_duplicates(name: str, url: str, text: str, out: Outcome) -> None:
    services = readme.services(text)
    key = name_key(name)
    for svc in services:
        other = name_key(svc.name)
        if other == key:
            out.errors.append(f"**{neutralize(svc.name)}** уже есть в списке. Если данные устарели, "
                              f"[сообщите об изменении]({REPO_URL}/issues/new?template=update-vpn.yml).")
            return
        if min(len(key), len(other)) >= 4 and (key in other or other in key or edit_distance(key, other) <= 1):
            out.warnings.append(f"Название похоже на уже добавленный сервис **{neutralize(svc.name)}** — "
                                f"проверьте, что это не подделка.")
    if url:
        ident = identity_key(url)
        if any(identity_key(t) == ident for t in link_targets(text) if t.startswith("https://")):
            out.errors.append(f"Сайт `{neutralize(ident)}` уже упоминается в README.")
            return
        label = registrable_domain(urlsplit(url).hostname or "").split(".")[0]
        for svc in services:
            other = registrable_domain(urlsplit(svc.url).hostname or "").split(".")[0]
            if len(label) >= 5 and label != other and edit_distance(label, other) <= 2:
                out.warnings.append(f"Домен похож на домен сервиса **{neutralize(svc.name)}** "
                                    f"(`{neutralize(identity_key(svc.url))}`) — проверьте, что это не фишинговая копия.")
    if readme.github_anchor(name) in {a for _, _, a in readme.headings(text)}:
        out.errors.append("**Название сервиса** совпадает с заголовком раздела README — уточните его.")


def evaluate(form: dict, policy: dict, readme_text: str) -> Outcome:
    out = Outcome()

    name = one_line(form.get("name"))
    if not NAME_RE.match(name):
        out.errors.append("**Название сервиса**: до 40 символов — буквы, цифры, пробелы и `. + & ' -`.")
    elif mixed_scripts(name):
        out.errors.append("**Название сервиса**: латиница и кириллица смешаны в одном слове.")

    url = one_line(form.get("url"))
    telegram_only = False
    if check_url("Официальный сайт", url, out, policy, required=True):
        telegram_only = (urlsplit(url).hostname or "").lower() == "t.me"
        if telegram_only:
            out.warnings.append("У сервиса нет сайта, только Telegram-бот.")
    check_duplicates(name, url, readme_text, out)

    telegram = one_line(form.get("telegram")).removeprefix("https://t.me/").removeprefix("@").strip("/")
    if telegram and not TELEGRAM_RE.match(telegram):
        out.errors.append("**Telegram-бот или канал**: укажите имя вида `@examplevpn_bot`.")

    summary = one_line(form.get("summary"))
    about = one_line(form.get("about"))
    free = one_line(form.get("free"))
    check_text("Описание для таблицы", summary, out, limit=LIMITS["summary"])
    check_text("Описание для обзора", about, out, limit=LIMITS["about"])
    if free:
        check_text("Бесплатный доступ", free, out, limit=LIMITS["free"], style=False)

    protocols = checked(form, "protocols", PROTOCOLS)
    if not protocols:
        out.errors.append("**Протоколы**: отметьте хотя бы один.")
    elif not any(o in OBFUSCATED for o, on in form["protocols"].items() if on):
        out.errors.append("**Протоколы**: в список принимаются только сервисы с обфускацией трафика — "
                          "стандартные WireGuard, OpenVPN и IPSec в России блокируются DPI.")
    platforms = checked(form, "platforms", PLATFORMS)
    if not platforms:
        out.errors.append("**Платформы**: отметьте хотя бы одну.")
    payment = checked(form, "payment", PAYMENT)
    if not payment:
        out.errors.append("**Способы оплаты**: отметьте хотя бы один.")

    price_month = check_price("Цена за месяц", form.get("price_month", ""), out, required=True)
    price_year = check_price("Цена за год", form.get("price_year", ""), out, required=False)
    is_free = price_month == "Бесплатно"
    pricing_url = one_line(form.get("pricing_url"))
    if pricing_url:
        check_url("Страница с ценами", pricing_url, out, policy, required=False)
    elif not is_free:
        out.errors.append("**Страница с ценами**: укажите официальную страницу с тарифами.")
    privacy_url = one_line(form.get("privacy_url"))
    check_url("Политика конфиденциальности", privacy_url, out, policy, required=True)

    repo = one_line(form.get("repo")).rstrip("/").removesuffix(".git")
    if repo and not REPO_RE.match(repo):
        out.errors.append("**Исходный код клиента**: ссылка должна выглядеть как `https://github.com/owner/repo`.")

    affiliation = AFFILIATION.get(one_line(form.get("affiliation")))
    if not affiliation:
        out.errors.append("**Ваша связь с сервисом**: выберите вариант.")
    elif affiliation != "none":
        out.labels.add("vendor-submitted")
    notes = str(form.get("notes") or "").strip()
    if len(one_line(notes)) < 30:
        out.errors.append("**Почему сервис стоит добавить**: расскажите подробнее, как вы проверяли работу в России.")
    elif len(notes) > LIMITS["notes"]:
        out.errors.append(f"**Почему сервис стоит добавить**: не длиннее {LIMITS['notes']} символов.")
    if not all((form.get("confirmations") or {"": False}).values()):
        out.errors.append("Отметьте все пункты в **Подтверждениях**.")

    if url and pricing_url and identity_key(pricing_url) != identity_key(url):
        out.warnings.append("Цены опубликованы не на сайте сервиса — проверьте источник.")
    if url and privacy_url and identity_key(privacy_url) != identity_key(url):
        out.warnings.append("Политика конфиденциальности опубликована не на сайте сервиса — проверьте, чья она.")

    out.facts["Telegram-only"] = telegram_only
    out.service = {
        "name": name, "url": url, "telegram": telegram, "summary": summary, "about": about,
        "protocols": protocols, "platforms": platforms, "free": free, "payment": payment,
        "price_month": price_month, "price_year": price_year if not is_free else "Бесплатно",
        "pricing_url": pricing_url, "privacy_url": privacy_url, "repo": repo, "affiliation": affiliation,
    }
    return out


# ---------------------------------------------------------------- сетевые проверки

def check_online(label: str, url: str, out: Outcome, *, primary: bool = False) -> None:
    result = fetch(url)
    if result.broken:
        out.errors.append(f"**{label}**: ссылка не открывается ({neutralize(result.describe())}).")
    elif result.unverifiable:
        out.warnings.append(f"**{label}** ответил {neutralize(result.describe())} — мейнтейнер проверит вручную.")
    if result.final_url and result.moved_domain:
        message = f"**{label}** перенаправляет на другой домен (`{neutralize(result.final_url)}`)."
        if primary:
            out.errors.append(message + " Укажите конечный адрес.")
        else:
            out.warnings.append(message)


def check_domain_age(url: str, policy: dict, out: Outcome, *, override: bool) -> None:
    domain = registrable_domain(urlsplit(url).hostname or "")
    registered = domain_registered(domain)
    min_age = int(policy.get("min_domain_age_days", 90))
    if registered is None:
        out.warnings.append(f"Не удалось узнать дату регистрации `{domain}` (у .ru и .рф её нет в RDAP) — "
                            f"мейнтейнер проверит вручную.")
        return
    age = (dt.datetime.now(dt.timezone.utc).date() - registered).days
    out.facts["домен зарегистрирован"] = registered.isoformat()
    if age < min_age:
        message = (f"Домену `{domain}` {age} дн. В список добавляются сервисы с доменом старше {min_age} дн. — "
                   f"так мы отсекаем однодневки.")
        (out.warnings if override else out.errors).append(message)


def run_online_checks(out: Outcome, policy: dict, gh: GitHub | None, *, override: bool) -> None:
    svc = out.service
    check_online("Официальный сайт", svc["url"], out, primary=True)
    if not out.facts.get("Telegram-only"):
        check_domain_age(svc["url"], policy, out, override=override)
    if svc["pricing_url"]:
        check_online("Страница с ценами", svc["pricing_url"], out)
    check_online("Политика конфиденциальности", svc["privacy_url"], out)
    if svc["repo"] and gh is not None:
        out.errors += [f"**Исходный код клиента**: {p}." for p in repo_health(gh, svc["repo"])]


# ---------------------------------------------------------------- тексты

def trust_failure_comment(trust: TrustResult) -> str:
    reasons = "\n".join(f"- {r}" for r in trust.errors)
    return ("### Заявка не принята\n\n"
            f"{reasons}\n\n"
            f"Эти правила защищают список от спама и подделок, подробнее — в [CONTRIBUTING.md]({REPO_URL}/blob/main/"
            "CONTRIBUTING.md#кто-может-предлагать-сервисы). Если это ошибка, напишите ниже — мейнтейнер может "
            "одобрить заявку вручную.")


def problems_comment(out: Outcome) -> str:
    lines = ["### Нужно исправить", "", *[f"- {e}" for e in out.errors]]
    if out.warnings:
        lines += ["", "**Заметки**", *[f"- {w}" for w in out.warnings]]
    lines += ["", "Отредактируйте заявку — проверка запустится заново автоматически."]
    return "\n".join(lines)


def preview(svc: dict) -> str:
    table = neutralize("\n".join([readme.TABLE_HEADER, "| :--- | :--- | :--- | :--- |", readme.render_row(svc)]))
    review = neutralize("\n".join(readme.render_review(svc)[:-2]))
    return "\n".join([table, "", "<details><summary>Обзор</summary>", "", review, "</details>"])


def success_comment(out: Outcome, pull_number: int) -> str:
    lines = [f"### Проверки пройдены — pull request #{pull_number}", "", preview(out.service), ""]
    if out.warnings:
        lines += ["Мейнтейнер дополнительно проверит:", *[f"- {w}" for w in out.warnings], ""]
    lines.append("Мейнтейнер посмотрит pull request в ближайшее время. Правки этой заявки обновляют его.")
    return "\n".join(lines)


def pr_body(out: Outcome, issue_number: int, author: str, trust: TrustResult) -> str:
    svc = out.service
    facts = {**trust.facts, **{k: v for k, v in out.facts.items() if k != "Telegram-only"}}
    lines = [
        f"Добавляет **{neutralize(svc['name'])}** по заявке #{issue_number} от @{author}.",
        "",
        "#### Как это будет выглядеть",
        "",
        preview(svc),
        "",
        "#### Автоматические проверки",
        "",
        "- Автор заявки: " + ("мейнтейнер" if trust.maintainer else
                              "проверки сняты мейнтейнером" if any("мейнтейнер" in f for f in trust.flags) else
                              "пройдено"),
        "- Ссылки, тексты, протоколы, дубликаты: пройдено",
        "- Сайт, цены и политика открываются, нет редиректа на другой домен, возраст домена: пройдено",
        "",
        "Ссылки для сверки: " + " · ".join(
            f"[{label}]({neutralize(link)})" for label, link in
            (("цены", svc["pricing_url"]), ("политика", svc["privacy_url"])) if link),
    ]
    review = out.warnings + [f"Автор: {f}" for f in trust.flags]
    if review:
        lines += ["", "#### Требует внимания", "", *[f"- {r}" for r in review]]
    if facts:
        lines += ["", "<details><summary>Факты</summary>", ""]
        lines += [f"- {k}: {neutralize(v)}" for k, v in facts.items()]
        lines += ["", "</details>"]
    if svc["affiliation"] != "none":
        lines += ["", f"> **Раскрытие:** автор заявки связан с сервисом (`{svc['affiliation']}`)."]
    lines += ["", f"Closes #{issue_number}"]
    return "\n".join(lines)


# ---------------------------------------------------------------- GitHub

def branch_for(issue_number: int) -> str:
    return f"submission/issue-{issue_number}"


def open_pull_request(gh: GitHub, out: Outcome, issue: dict, trust: TrustResult, base: str, base_sha: str,
                      base_text: str) -> dict:
    number, author = issue["number"], issue["user"]["login"]
    branch = branch_for(number)
    name = out.service["name"]
    new_text = readme.insert_service(base_text, out.service)
    title = f"Добавить VPN: {name}"
    message = (f"{title}\n\nЗаявка #{number}.\n\n"
               f"Co-authored-by: {author} <{issue['user']['id']}+{author}@users.noreply.github.com>")
    gh.commit_files(branch, base_sha, {"README.md": new_text}, message, create=gh.branch_sha(branch) is None)
    body = pr_body(out, number, author, trust)
    pull = gh.open_pull_for(branch)
    if pull:
        gh.update_pull(pull["number"], title, body)
    else:
        pull = gh.create_pull(branch, base, title, body)
    labels = {"new-service", *out.labels}
    if trust.flags or out.warnings:
        labels.add("needs-manual-review")
    gh.add_labels(pull["number"], sorted(labels))
    return pull


def run(event: dict, gh: GitHub | None, *, online: bool, dry_run: bool) -> int:
    issue = event.get("issue") or {}
    if "pull_request" in issue:
        return 0
    labels = {label["name"] for label in issue.get("labels", [])}
    if SUBMISSION_LABEL not in labels:
        print("Это не заявка; пропускаю.")
        return 0
    action = event.get("action")
    if action == "labeled" and (event.get("label") or {}).get("name") != OVERRIDE_LABEL:
        return 0

    number, author = issue["number"], issue["user"]["login"]
    policy = load_policy()
    override = OVERRIDE_LABEL in labels and gh is not None and label_applied_by_maintainer(gh, number, OVERRIDE_LABEL)
    if issue.get("state") == "closed":
        if not (action == "labeled" and override):
            print("Issue закрыт; пропускаю.")
            return 0
        if not dry_run:
            gh.set_state(number, "open")

    if override:
        trust = TrustResult(login=author, trusted=True, flags=["проверки автора сняты мейнтейнером"])
    elif gh is None:
        trust = TrustResult(login=author, trusted=True, flags=["локальный прогон: проверки автора пропущены"])
    else:
        trust = assess(gh, policy, author, issue.get("author_association", "NONE"),
                       kind="new_service", exclude_issue=number)
    if not trust.ok:
        print("Проверка автора не пройдена:", *trust.errors, sep="\n- ")
        if not dry_run:
            gh.upsert_comment(number, MARKER, trust_failure_comment(trust))
            gh.add_labels(number, ["trust-check-failed"])
            gh.set_state(number, "closed", "not_planned")
        return 0

    base = (event.get("repository") or {}).get("default_branch", "main")
    if gh is not None:
        base_sha = gh.branch_sha(base)
        base_text = gh.file_at(gh.repo, "README.md", base_sha)
        if base_text is None:
            raise GitHubError(404, f"README.md не найден в {base}")
    else:
        base_sha, base_text = "", readme.read()

    out = evaluate(parse(issue.get("body") or "", TEMPLATE), policy, base_text)
    if online and not out.errors:
        run_online_checks(out, policy, gh, override=override)
    if not out.errors:
        try:
            readme.insert_service(base_text, out.service)
        except ValueError as exc:
            raise SystemExit(f"Разметка README сломана, бот не может вставить сервис: {exc}") from None

    if out.errors:
        print(problems_comment(out))
        if not dry_run:
            pull = gh.open_pull_for(branch_for(number))
            if pull:
                gh.close_pull(pull["number"])
            gh.upsert_comment(number, MARKER, problems_comment(out))
            gh.remove_label(number, "pr-opened")
            gh.add_labels(number, ["needs-changes"])
        return 0

    print("Проверки пройдены.\n" + preview(out.service))
    if dry_run:
        print(pr_body(out, number, author, trust))
        return 0
    pull = open_pull_request(gh, out, issue, trust, base, base_sha, base_text)
    gh.remove_label(number, "needs-changes")
    gh.add_labels(number, ["pr-opened"])
    gh.upsert_comment(number, MARKER, success_comment(out, pull["number"]))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_PATH"), help="путь к JSON события")
    parser.add_argument("--dry-run", action="store_true", help="ничего не писать в GitHub")
    parser.add_argument("--offline", action="store_true", help="без GitHub API и сетевых проверок (включает --dry-run)")
    args = parser.parse_args()
    if not args.event:
        parser.error("нужен --event или GITHUB_EVENT_PATH")
    event = json.loads(Path(args.event).read_text(encoding="utf-8"))
    gh = None if args.offline else GitHub(os.environ.get("GITHUB_REPOSITORY", "kozlovartem20201/awesome-vpn-list-russia"))
    try:
        return run(event, gh, online=not args.offline, dry_run=args.dry_run or args.offline)
    except GitHubError as exc:
        print(f"Ошибка GitHub API: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
