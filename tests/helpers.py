"""Общие заготовки для тестов: python -m unittest discover -s tests"""

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from issue_forms import form_fields  # noqa: E402
from submission import TEMPLATE  # noqa: E402

GOOD = {
    "name": "Example VPN",
    "url": "https://example-vpn.com/",
    "telegram": "@examplevpn_bot",
    "summary": "VLESS Reality и Hysteria2, 20 стран, до 5 устройств, оплата российскими картами.",
    "about": "Сервис работает с 2023 года и держит серверы в Финляндии и Нидерландах. Клиент сам выбирает протокол.",
    "protocols": ["VLESS Reality (XRay)", "Hysteria2 или TUIC"],
    "platforms": ["Android", "iOS", "Windows"],
    "free": "7 дней бесплатно",
    "payment": ["Российские карты (МИР) или СБП", "Криптовалюта"],
    "price_month": "200 ₽",
    "price_year": "1920₽",
    "pricing_url": "https://example-vpn.com/pricing",
    "privacy_url": "https://example-vpn.com/privacy",
    "repo": "",
    "affiliation": "Не связан(а) с сервисом",
    "notes": "Проверял в Москве на МТС и Ростелекоме 10.10.2026, работает стабильно уже полгода.",
    "confirmations": None,  # None — отметить все
}


def issue_body(**overrides) -> str:
    """Тело issue так, как его генерирует GitHub из формы add-vpn.yml."""
    values = {**GOOD, **overrides}
    parts = []
    for f in form_fields(TEMPLATE):
        value = values.get(f.id)
        if f.type == "checkboxes":
            chosen = f.options if value is None else value
            text = "\n".join(f"- [{'X' if o in chosen else ' '}] {o}" for o in f.options)
        else:
            text = value or "_No response_"
        parts.append(f"### {f.label}\n\n{text}")
    return "\n\n".join(parts)


def issue_event(action="opened", number=42, author="contributor", labels=("submission",), **overrides) -> dict:
    return {
        "action": action,
        "issue": {
            "number": number, "state": "open", "author_association": "NONE",
            "user": {"login": author, "id": 1001, "type": "User"},
            "labels": [{"name": n} for n in labels],
            "body": issue_body(**overrides),
        },
        "repository": {"default_branch": "main"},
    }


class FakeGitHub:
    """Подменяет GitHub API для проверок автора и стража PR."""

    repo = "kozlovartem20201/awesome-vpn-list-russia"

    def __init__(self, created_days_ago=400, repos=5, followers=3, merged=0, open_items=(), recent=0,
                 permissions=None, user_type="User", files=(), contents=None, events=()):
        self.created = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=created_days_ago)).isoformat()
        self.repos, self.followers, self.user_type = repos, followers, user_type
        self.merged, self.open_items, self.recent = merged, list(open_items), recent
        self.permissions = permissions or {}
        self._files, self._contents, self._events = list(files), contents or {}, list(events)

    def user(self, login):
        return {"login": login, "type": self.user_type, "created_at": self.created.replace("+00:00", "Z"),
                "public_repos": self.repos, "followers": self.followers}

    def permission(self, login):
        return self.permissions.get(login, "read")

    def search_count(self, query):
        if "is:merged" in query:
            return self.merged, []
        if "is:open" in query:
            return len(self.open_items), [{"number": n} for n in self.open_items]
        return self.recent, []

    def issue_events(self, number):
        return self._events

    def pull_files(self, number):
        return self._files

    def file_at(self, repo, path, ref):
        return self._contents.get((ref, path))
