"""Офлайн-правила для ссылок и текста.

Проверки не ходят в сеть, поэтому выполняются в CI для всего README и в боте
заявок до любых сетевых запросов.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import parse_qsl, urlsplit

LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]*)\)")
# Цель любой Markdown-ссылки, в том числе внешней ссылки вокруг картинки: [![alt](img)](url).
LINK_TARGET_RE = re.compile(r"\]\(([^)\s]+)\)")

SHORTENER_HOSTS = {
    "bit.ly", "bitly.com", "buff.ly", "cutt.ly", "goo.gl", "is.gd", "linktr.ee",
    "lnkd.in", "ow.ly", "rb.gy", "rebrand.ly", "shorturl.at", "t.co", "t.ly",
    "tiny.cc", "tinyurl.com", "trib.al", "v.gd", "s.id", "short.io", "bl.ink",
    "clck.ru", "vk.cc", "u.to", "qps.ru", "goo.su", "taplink.cc",
}

# Параметры, в которых передают метки трекинга, партнёрские и реферальные коды.
TRACKING_PARAM_RE = re.compile(
    r"^(utm_\w+|ref|ref_?id|ref_?code|referral(_?code)?|referrer|aff(_?id)?|affiliate(_?id)?|"
    r"via|invite(_?code)?|inviter(_?id)?|partner(_?id)?|promo(_?code)?|coupon|fbclid|gclid|"
    r"dclid|msclkid|yclid|twclid|ttclid|mc_cid|mc_eid|_hsenc|_hsmi|irclickid|clickid|subid|"
    r"campaign(_?id)?|start|startapp|erid)$",
    re.IGNORECASE,
)
REFERRAL_PATH_RE = re.compile(r"/(ref|referral|referrals|invite|aff|affiliate|affiliates|partner|join/[A-Za-z0-9]{4,})(/|$)", re.I)

# Невидимые символы и управление направлением текста маскируют подмену ссылок.
HIDDEN_CHARS_RE = re.compile("[​-‏‪-‮⁠-⁤⁦-⁩﻿]")

PROMO_WORDS_RE = re.compile(
    r"\b(лучш\w*|сам(ый|ая|ое|ые|ых)\s+(быстр|надёжн|надежн|дешёв|дешев|стабильн|безопасн)\w*|"
    r"номер\s+один|уникальн\w*|революцион\w*|непревзойд\w*|идеальн\w*|невероятн\w*|топов\w*|"
    r"best|ultimate)\b|№\s?1\b|#1\b|100\s?%",
    re.IGNORECASE,
)

MULTI_LABEL_SUFFIXES = {
    "co.uk", "org.uk", "com.au", "co.jp", "co.kr", "com.br", "com.cn", "com.hk",
    "com.sg", "co.in", "co.nz", "com.tr", "co.za", "com.tw",
    "com.ru", "net.ru", "org.ru", "pp.ru", "msk.ru", "spb.ru", "com.ua", "com.kz",
}
# Хостинги, где каждый поддомен принадлежит разным владельцам.
PRIVATE_SUFFIXES = {
    "github.io", "gitbook.io", "readthedocs.io", "vercel.app", "netlify.app",
    "pages.dev", "herokuapp.com", "web.app", "firebaseapp.com", "notion.site",
    "webflow.io", "framer.website", "tilda.ws", "telegra.ph",
}
# Хосты, где владелец определяется путём, а не именем: сколько сегментов пути брать.
PATH_HOSTS = {"github.com": 2, "gitlab.com": 2, "codeberg.org": 2, "t.me": 1, "s3.amazonaws.com": 1}


def registrable_domain(host: str) -> str:
    host = host.lower().rstrip(".")
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    last_two = ".".join(labels[-2:])
    if last_two in MULTI_LABEL_SUFFIXES or last_two in PRIVATE_SUFFIXES:
        return ".".join(labels[-3:])
    return last_two


def identity_key(url: str) -> str:
    """Ключ для поиска дубликатов: домен, а на t.me, GitHub и т. п. — ещё и путь."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    path_host = host if host in PATH_HOSTS else registrable_domain(host)
    if path_host in PATH_HOSTS:
        segments = [s for s in parts.path.split("/") if s][:PATH_HOSTS[path_host]]
        return f"{path_host}/{'/'.join(segments).lower()}"
    return registrable_domain(host)


def url_problems(url: str) -> list[str]:
    """Нарушения правил для одной ссылки (пустой список, если всё в порядке)."""
    problems: list[str] = []
    if HIDDEN_CHARS_RE.search(url):
        return ["содержит невидимые Unicode-символы"]
    if any(ch.isspace() for ch in url):
        return ["содержит пробелы"]
    parts = urlsplit(url)
    if parts.scheme != "https":
        problems.append("должна начинаться с https://")
    host = parts.hostname or ""
    if not host:
        return problems + ["не содержит имени сайта"]
    if parts.username or parts.password:
        problems.append("не должна содержать логин или пароль")
    if parts.port not in (None, 443):
        problems.append("не должна указывать нестандартный порт")
    if not host.isascii() or any(label.startswith("xn--") for label in host.split(".")):
        problems.append("домены в punycode и с нелатинскими буквами не принимаются")
    try:
        ipaddress.ip_address(host)
        problems.append("должна вести на домен, а не на IP-адрес")
    except ValueError:
        pass
    if "." not in host:
        problems.append("должна вести на публичный домен")
    if registrable_domain(host) in SHORTENER_HOSTS or host in SHORTENER_HOSTS:
        problems.append("сокращатели ссылок не принимаются — укажите конечный адрес")
    for key, _ in parse_qsl(parts.query, keep_blank_values=True):
        if TRACKING_PARAM_RE.match(key):
            problems.append(f"уберите параметр трекинга или реферальный код `{key}`")
    if REFERRAL_PATH_RE.search(parts.path):
        problems.append("реферальные и партнёрские ссылки не принимаются")
    return problems


def links_in(text: str) -> list[tuple[str, str]]:
    return LINK_RE.findall(text or "")


def link_targets(text: str) -> list[str]:
    return LINK_TARGET_RE.findall(text or "")


def text_problems(text: str, *, max_length: int, multiline: bool = False) -> list[str]:
    """Структурные правила для текста, который попадёт в README."""
    if not isinstance(text, str) or not text.strip():
        return ["не заполнено"]
    problems: list[str] = []
    if HIDDEN_CHARS_RE.search(text):
        problems.append("содержит невидимые Unicode-символы")
    if not multiline and ("\n" in text or "\r" in text):
        problems.append("должно быть одной строкой")
    if "|" in text:
        problems.append("не должно содержать символ `|` — он ломает таблицу")
    if re.search(r"<[a-zA-Z/!]", text):
        problems.append("не должно содержать HTML")
    if re.search(r"https?://|\]\(|www\.", text, re.I):
        problems.append("не должно содержать ссылок — для них есть отдельные поля формы")
    if "@" in text:
        problems.append("не должно содержать `@` — Telegram укажите в отдельном поле")
    if re.search(r"^\s*(#|>|-\s|\*\s|\d+\.\s)", text, re.M) or re.search(r"[\[\]`\\]", text):
        problems.append("не должно содержать Markdown-разметку (заголовки, списки, цитаты, `[ ]`, обратные кавычки)")
    if len(text) > max_length:
        problems.append(f"длиннее {max_length} символов (сейчас {len(text)})")
    return problems


def style_problems(text: str) -> list[str]:
    """Редакционные правила для описаний новых сервисов."""
    problems: list[str] = []
    if not text.rstrip().endswith("."):
        problems.append("должно заканчиваться точкой")
    match = PROMO_WORDS_RE.search(text)
    if match:
        problems.append(f"содержит рекламную формулировку («{match.group(0)}») — пишите фактами")
    if "!" in text:
        problems.append("не должно содержать восклицательных знаков")
    letters = [ch for ch in text if ch.isalpha()]
    if letters and sum(ch.isupper() for ch in letters) > max(12, len(letters) // 4):
        problems.append("слишком много заглавных букв")
    return problems
