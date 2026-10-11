"""Проверка автора заявки или pull request'а.

Жёсткие ошибки (errors) останавливают автоматизацию. Мягкие сигналы (flags)
пропускают заявку, но помечают PR для внимательной ручной проверки.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from github_api import GitHub

MAINTAINER_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
MAINTAINER_ROLES = {"admin", "maintain", "write"}


@dataclass
class TrustResult:
    login: str
    maintainer: bool = False
    trusted: bool = False  # мейнтейнер или уже есть смёрженный PR здесь
    errors: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    facts: dict[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def blocked(policy: dict, login: str) -> bool:
    return login.lower() in {u.lower() for u in policy.get("blocked_users") or []}


def assess(gh: GitHub, policy: dict, login: str, association: str, *, kind: str,
           exclude_issue: int | None = None, check_limits: bool = True,
           now: dt.datetime | None = None) -> TrustResult:
    """kind — "new_service" или "update". Лимиты заявок нужны только для issue-форм."""
    now = now or dt.datetime.now(dt.timezone.utc)
    result = TrustResult(login=login)

    if blocked(policy, login):
        result.errors.append("Этому аккаунту запрещено присылать изменения в список.")
        return result

    if association in MAINTAINER_ASSOCIATIONS or gh.permission(login) in MAINTAINER_ROLES:
        result.maintainer = result.trusted = True
        return result

    user = gh.user(login)
    if user.get("type") != "User":
        result.errors.append("Заявки принимаются только от личных аккаунтов GitHub, не от ботов и организаций.")
        return result

    created = dt.datetime.fromisoformat(user["created_at"].replace("Z", "+00:00"))
    age = (now - created).days
    repos, followers = int(user.get("public_repos", 0)), int(user.get("followers", 0))
    result.facts.update({"аккаунт создан": created.date().isoformat(),
                         "публичных репозиториев": repos, "подписчиков": followers})

    merged, _ = gh.search_count(f"repo:{gh.repo} is:pr is:merged author:{login}")
    result.facts["смёржено PR здесь"] = merged
    result.trusted = merged > 0

    min_age = int((policy.get("min_account_age_days") or {}).get(kind, 30))
    if not result.trusted and age < min_age:
        allowed_from = (created + dt.timedelta(days=min_age)).date().isoformat()
        what = "новые сервисы" if kind == "new_service" else "правки"
        result.errors.append(
            f"Вашему аккаунту GitHub {age} дн., а {what} принимаются от аккаунтов старше {min_age} дн. "
            f"(кроме авторов, у которых уже есть смёрженный PR здесь). Попробуйте снова с {allowed_from} "
            f"или дождитесь, пока заявку одобрит мейнтейнер.")

    if check_limits:
        limits = policy.get("limits") or {}
        _, open_items = gh.search_count(f"repo:{gh.repo} is:issue is:open label:submission author:{login}")
        open_others = [i for i in open_items if i.get("number") != exclude_issue]
        max_open = int(limits.get("max_open_submissions", 1))
        if len(open_others) >= max_open:
            refs = ", ".join(f"#{i['number']}" for i in open_others[:5])
            result.errors.append(f"У вас уже есть открытые заявки ({refs}). Дождитесь, пока их рассмотрят.")
        since = (now - dt.timedelta(days=30)).date().isoformat()
        recent, _ = gh.search_count(f"repo:{gh.repo} is:issue label:submission author:{login} created:>={since}")
        max_recent = int(limits.get("max_submissions_per_30_days", 3))
        if recent > max_recent:
            result.errors.append(f"За последние 30 дней вы прислали {recent} заявок (лимит {max_recent}).")

    anomalies = policy.get("anomalies") or {}
    if repos >= int(anomalies.get("many_repos_no_followers", 200)) and followers == 0:
        result.flags.append(f"{repos} публичных репозиториев и ни одного подписчика")
    if age < int(anomalies.get("young_account_days", 180)) and repos == 0:
        result.flags.append("молодой аккаунт без публичных репозиториев")
    return result


def label_applied_by_maintainer(gh: GitHub, issue_number: int, label: str) -> bool:
    """True, если последней метку поставил человек с правом записи."""
    actor = None
    for event in gh.issue_events(issue_number):
        if event.get("event") == "labeled" and event.get("label", {}).get("name") == label:
            actor = (event.get("actor") or {}).get("login")
    return bool(actor) and gh.permission(actor) in MAINTAINER_ROLES
