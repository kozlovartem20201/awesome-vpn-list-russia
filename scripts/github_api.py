"""Минимальный клиент GitHub REST API на стандартной библиотеке.

Обёрнуты только нужные ботам методы. Токен берётся из GH_TOKEN и не логируется.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API = "https://api.github.com"
USER_AGENT = "awesome-vpn-list-russia-bot"


class GitHubError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"GitHub API {status}: {message}")
        self.status = status


class GitHub:
    def __init__(self, repo: str, token: str | None = None):
        self.repo = repo
        self.owner = repo.split("/")[0]
        self.token = token if token is not None else os.environ.get("GH_TOKEN", "")

    # -- транспорт ---------------------------------------------------------
    def request(self, method: str, path: str, body: Any = None, params: dict | None = None) -> Any:
        url = path if path.startswith("https://") else f"{API}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if data is not None:
            headers["Content-Type"] = "application/json"
        for attempt in range(3):
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:500]
                if exc.code in (502, 503, 504) and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise GitHubError(exc.code, detail) from None
            except urllib.error.URLError:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise
        raise AssertionError("unreachable")

    def r(self, suffix: str) -> str:
        return f"/repos/{self.repo}{suffix}"

    # -- пользователи и права ---------------------------------------------
    def user(self, login: str) -> dict:
        return self.request("GET", f"/users/{urllib.parse.quote(login)}")

    def permission(self, login: str) -> str:
        """admin/maintain/write/triage/read/none. Любая ошибка — "none" (только строже)."""
        try:
            data = self.request("GET", self.r(f"/collaborators/{urllib.parse.quote(login)}/permission"))
        except GitHubError as exc:
            if exc.status in (401, 403, 404):
                return "none"
            raise
        return data.get("role_name") or data.get("permission") or "none"

    def search_count(self, query: str) -> tuple[int, list[dict]]:
        data = self.request("GET", "/search/issues", params={"q": query, "per_page": 20})
        return data.get("total_count", 0), data.get("items", [])

    # -- issues и комментарии ----------------------------------------------
    def issue_events(self, number: int) -> list[dict]:
        return self._paged(self.r(f"/issues/{number}/events"))

    def comments(self, number: int) -> list[dict]:
        return self._paged(self.r(f"/issues/{number}/comments"))

    def _paged(self, path: str) -> list[dict]:
        items, page = [], 1
        while True:
            batch = self.request("GET", path, params={"per_page": 100, "page": page})
            items.extend(batch)
            if len(batch) < 100:
                return items
            page += 1

    def upsert_comment(self, number: int, marker: str, body: str) -> None:
        """Один комментарий бота на issue или PR, опознаётся по HTML-маркеру."""
        body = f"{marker}\n{body}"
        for comment in self.comments(number):
            if marker in (comment.get("body") or "") and comment.get("user", {}).get("type") == "Bot":
                if comment["body"] != body:
                    self.request("PATCH", self.r(f"/issues/comments/{comment['id']}"), {"body": body})
                return
        self.request("POST", self.r(f"/issues/{number}/comments"), {"body": body})

    def add_labels(self, number: int, labels: list[str]) -> None:
        if labels:
            self.request("POST", self.r(f"/issues/{number}/labels"), {"labels": labels})

    def remove_label(self, number: int, label: str) -> None:
        try:
            self.request("DELETE", self.r(f"/issues/{number}/labels/{urllib.parse.quote(label)}"))
        except GitHubError as exc:
            if exc.status != 404:
                raise

    def set_state(self, number: int, state: str, reason: str | None = None) -> None:
        body = {"state": state}
        if reason:
            body["state_reason"] = reason
        self.request("PATCH", self.r(f"/issues/{number}"), body)

    def create_issue(self, title: str, body: str, labels: list[str]) -> dict:
        return self.request("POST", self.r("/issues"), {"title": title, "body": body, "labels": labels})

    def update_issue_body(self, number: int, body: str) -> None:
        self.request("PATCH", self.r(f"/issues/{number}"), {"body": body})

    # -- git и pull request'ы ----------------------------------------------
    def branch_sha(self, branch: str) -> str | None:
        try:
            ref = self.request("GET", self.r(f"/git/ref/heads/{urllib.parse.quote(branch)}"))
        except GitHubError as exc:
            if exc.status == 404:
                return None
            raise
        return ref["object"]["sha"]

    def file_at(self, repo: str, path: str, ref: str) -> str | None:
        """Содержимое файла в любом репозитории (в том числе в форке автора PR)."""
        try:
            data = self.request("GET", f"/repos/{repo}/contents/{urllib.parse.quote(path)}", params={"ref": ref})
        except GitHubError as exc:
            if exc.status == 404:
                return None
            raise
        if not isinstance(data, dict) or data.get("type") != "file":
            return None
        return base64.b64decode(data["content"]).decode("utf-8")

    def commit_files(self, branch: str, base_sha: str, files: dict[str, str], message: str,
                     *, create: bool) -> str:
        """Один коммит поверх base_sha; ветка перенаправляется на него (force)."""
        base_commit = self.request("GET", self.r(f"/git/commits/{base_sha}"))
        tree = self.request("POST", self.r("/git/trees"), {
            "base_tree": base_commit["tree"]["sha"],
            "tree": [{"path": p, "mode": "100644", "type": "blob", "content": c} for p, c in files.items()],
        })
        commit = self.request("POST", self.r("/git/commits"), {
            "message": message, "tree": tree["sha"], "parents": [base_sha],
        })
        if create:
            self.request("POST", self.r("/git/refs"), {"ref": f"refs/heads/{branch}", "sha": commit["sha"]})
        else:
            self.request("PATCH", self.r(f"/git/refs/heads/{urllib.parse.quote(branch)}"),
                         {"sha": commit["sha"], "force": True})
        return commit["sha"]

    def open_pull_for(self, branch: str) -> dict | None:
        pulls = self.request("GET", self.r("/pulls"), params={"head": f"{self.owner}:{branch}", "state": "open"})
        return pulls[0] if pulls else None

    def create_pull(self, branch: str, base: str, title: str, body: str) -> dict:
        return self.request("POST", self.r("/pulls"), {
            "title": title, "head": branch, "base": base, "body": body, "maintainer_can_modify": True,
        })

    def update_pull(self, number: int, title: str, body: str) -> None:
        self.request("PATCH", self.r(f"/pulls/{number}"), {"title": title, "body": body})

    def close_pull(self, number: int) -> None:
        self.request("PATCH", self.r(f"/pulls/{number}"), {"state": "closed"})

    def pull_files(self, number: int) -> list[dict]:
        return self._paged(self.r(f"/pulls/{number}/files"))

    def repo_info(self, full_name: str) -> dict | None:
        try:
            return self.request("GET", f"/repos/{full_name}")
        except GitHubError as exc:
            if exc.status in (404, 451):
                return None
            raise


def neutralize(text: object) -> str:
    """Безопасно для эха в комментарии: без упоминаний и HTML."""
    return (
        str(text)
        .replace("@", "@​")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
