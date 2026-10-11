"""Разбор и правка README.md: таблица сервисов, обзоры, оглавление и якоря заголовков.

README остаётся главным источником правды и редактируется руками. Бот заявок
дописывает новый сервис в трёх местах, которые размечены HTML-комментариями:
строку в конец основной таблицы, обзор — перед `<!-- reviews:end -->`, пункт
оглавления — в конец списка обзоров.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

SERVICES_START = "<!-- services:start"
SERVICES_END = "<!-- services:end"
REVIEWS_END = "<!-- reviews:end"
REVIEWS_HEADING = "Подробные обзоры каждого сервиса"
TABLE_HEADER = "| Сервис | Описание | Цена/мес | Цена/год |"

ROW_RE = re.compile(r"^\|\s*\*\*\[(?P<name>[^\]]+)\]\((?P<url>[^)\s]+)\)\*\*\s*\|")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


@dataclass
class Service:
    name: str
    url: str
    line: int  # номер строки, с единицы
    in_main_table: bool


def read(path: Path = README) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def github_anchor(heading: str) -> str:
    """Якорь заголовка так, как его строит GitHub (github-slugger)."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading.strip()).lower()
    out = []
    for ch in text:
        if ch == " ":
            out.append("-")
        elif ch in "-_" or unicodedata.category(ch)[0] in "LMN":
            out.append(ch)
    return "".join(out)


def headings(text: str) -> list[tuple[int, str, str]]:
    """[(уровень, текст, якорь)] с суффиксами -1, -2 для повторов, как на GitHub."""
    result, seen, fenced = [], {}, False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        m = HEADING_RE.match(line)
        if fenced or not m:
            continue
        base = github_anchor(m.group(2))
        count = seen.get(base, 0)
        seen[base] = count + 1
        result.append((len(m.group(1)), m.group(2), base if count == 0 else f"{base}-{count}"))
    return result


def marker_index(lines: list[str], marker: str) -> int:
    found = [i for i, line in enumerate(lines) if line.strip().startswith(marker)]
    if len(found) != 1:
        raise ValueError(f"в README должен быть ровно один комментарий `{marker} … -->`, найдено {len(found)}")
    return found[0]


def services(text: str) -> list[Service]:
    lines = text.split("\n")
    try:
        start, end = marker_index(lines, SERVICES_START), marker_index(lines, SERVICES_END)
    except ValueError:
        start = end = -1
    out = []
    for i, line in enumerate(lines):
        m = ROW_RE.match(line)
        if m:
            out.append(Service(m.group("name").strip(), m.group("url"), i + 1, start < i < end))
    return out


def split_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def review_headings(text: str) -> list[str]:
    """Заголовки ### внутри раздела обзоров."""
    lines = text.split("\n")
    end = marker_index(lines, REVIEWS_END)
    start = next((i for i, line in enumerate(lines) if line.startswith("## ") and REVIEWS_HEADING in line), None)
    if start is None or start > end:
        raise ValueError(f"перед комментарием reviews:end должен быть заголовок «## … {REVIEWS_HEADING}»")
    return [m.group(2) for line in lines[start:end] if (m := HEADING_RE.match(line)) and m.group(1) == "###"]


def reviews_anchor(text: str) -> str:
    for level, title, anchor in headings(text):
        if level == 2 and REVIEWS_HEADING in title:
            return anchor
    raise ValueError(f"в README нет заголовка «{REVIEWS_HEADING}»")


# ---------------------------------------------------------------- рендер

def site_label(url: str) -> str:
    """blancvpn.pro/ru — подпись ссылки на сайт без схемы, www и хвостового слэша."""
    label = re.sub(r"^https://(www\.)?", "", url).rstrip("/")
    return label.split("?")[0].split("#")[0]


def render_row(svc: dict) -> str:
    return f"| **[{svc['name']}]({svc['url']})** | {svc['summary']} | {svc['price_month']} | {svc['price_year']} |"


def render_review(svc: dict) -> list[str]:
    head = f"**Сайт:** [{site_label(svc['url'])}]({svc['url']})"
    if svc.get("telegram"):
        head += f" | **Telegram:** [@{svc['telegram']}](https://t.me/{svc['telegram']})"
    facts = [
        f"- **Протоколы:** {', '.join(svc['protocols'])}",
        f"- **Платформы:** {', '.join(svc['platforms'])}",
    ]
    if svc.get("free"):
        facts.append(f"- **Бесплатный доступ:** {svc['free']}")
    facts.append(f"- **Оплата:** {', '.join(svc['payment'])}")
    facts.append(f"- **Цены:** {price_phrase(svc['price_month'], svc['price_year'])}")
    facts.append(f"- **Политика конфиденциальности:** [{site_label(svc['privacy_url'])}]({svc['privacy_url']})")
    if svc.get("repo"):
        facts.append(f"- **Исходный код:** [{site_label(svc['repo']).removeprefix('github.com/')}]({svc['repo']})")
    return [f"### {svc['name']}", "", head, "", svc["about"], "", "**Ключевые характеристики:**", *facts, "", "---", ""]


def price_phrase(month: str, year: str) -> str:
    if month.lower() == "бесплатно":
        return "бесплатно"
    period = re.match(r"^(.+?)\s?/\s?(\d+)\s?мес$", month)  # «2390₽ / 6 мес»
    phrase = f"{period.group(1)} за {period.group(2)} мес." if period else f"{month} в месяц"
    return phrase if year in ("", "—") else f"{phrase}, {year} в год"


def toc_item(name: str) -> str:
    return f"  - [{name}](#{github_anchor(name)})"


# ---------------------------------------------------------------- правка

def insert_service(text: str, svc: dict) -> str:
    """Добавляет строку таблицы, обзор и пункт оглавления. Ошибки разметки — ValueError."""
    lines = text.split("\n")
    toc_anchor = f"](#{reviews_anchor(text)})"

    reviews_end = marker_index(lines, REVIEWS_END)
    lines[reviews_end:reviews_end] = render_review(svc)

    table_end = marker_index(lines, SERVICES_END)
    last_row = max((i for i in range(table_end) if lines[i].startswith("|")), default=-1)
    if last_row < marker_index(lines, SERVICES_START):
        raise ValueError("между комментариями services:start и services:end нет таблицы")
    lines.insert(last_row + 1, render_row(svc))

    toc = next((i for i, line in enumerate(lines) if line.startswith("- [") and toc_anchor in line), None)
    if toc is None:
        raise ValueError("в оглавлении нет пункта со ссылкой на раздел обзоров")
    j = toc + 1
    while j < len(lines) and lines[j].startswith("  - "):
        j += 1
    lines.insert(j, toc_item(svc["name"]))
    return "\n".join(lines)
