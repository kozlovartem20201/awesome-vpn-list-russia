import contextlib
import io
import json
import unittest

from helpers import FakeGitHub
import pr_guard
import readme

BASE = readme.read()
ROW = "| **[Example VPN](https://example-vpn.com/)** | Тест. | 200₽ | — |"


def with_row(text: str, row: str = ROW) -> str:
    return text.replace("\n\n<!-- services:end", f"\n{row}\n\n<!-- services:end", 1)


def pr_event(author="contributor", body=""):
    return {"action": "opened", "pull_request": {
        "number": 7, "state": "open", "body": body, "author_association": "NONE",
        "user": {"login": author, "type": "User"},
        "head": {"sha": "head", "repo": {"full_name": "contributor/awesome-vpn-list-russia"}},
        "base": {"sha": "base"},
    }}


def guard(head_text=None, files=("README.md",), event=None, **kw):
    contents = {("base", "README.md"): BASE, ("head", "README.md"): head_text or BASE}
    gh = FakeGitHub(files=[{"filename": f, "status": "modified"} for f in files], contents=contents, **kw)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        pr_guard.run(event or pr_event(), gh, dry_run=True)
    output = buffer.getvalue()
    return json.loads(output[output.index("{"):])


class PrGuardTest(unittest.TestCase):
    def test_first_timer_adding_service_is_sent_to_form(self):
        result = guard(with_row(BASE))
        self.assertTrue(result["close"])
        self.assertIn("use-submission-form", result["labels"])

    def test_trusted_contributor_may_add_service(self):
        result = guard(with_row(BASE), merged=1)
        self.assertFalse(result["close"])
        self.assertTrue(any("example-vpn.com" in n for n in result["notes"]))

    def test_young_account_is_closed(self):
        result = guard(BASE, created_days_ago=2)
        self.assertTrue(result["close"])
        self.assertIn("trust-check-failed", result["labels"])

    def test_domain_change_is_labelled(self):
        head = BASE.replace("(https://blancvpn.pro/ru)**", "(https://blancvpn-login.pro/ru)**")
        result = guard(head)
        self.assertFalse(result["close"])
        self.assertIn("domain-change", result["labels"])

    def test_referral_link_is_a_problem(self):
        head = BASE.replace("(https://papervpn.io)**", "(https://papervpn.io/?ref=abc)**")
        result = guard(head)
        self.assertTrue(any("`ref`" in p for p in result["problems"]))
        self.assertIn("needs-changes", result["labels"])

    def test_sensitive_files_and_vendor(self):
        body = "- [x] Я владелец или сотрудник сервиса"
        result = guard(files=(".github/workflows/ci.yml", "README.md"), event=pr_event(body=body))
        self.assertIn("sensitive-change", result["labels"])
        self.assertIn("vendor-submitted", result["labels"])

    def test_maintainer_is_skipped(self):
        contents = {("base", "README.md"): BASE, ("head", "README.md"): with_row(BASE)}
        gh = FakeGitHub(files=[{"filename": "README.md", "status": "modified"}], contents=contents,
                        permissions={"owner": "admin"})
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            pr_guard.run(pr_event(author="owner"), gh, dry_run=True)
        self.assertIn("мейнтейнера", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
