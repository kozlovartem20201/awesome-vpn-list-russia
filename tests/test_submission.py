import io
import unittest
from contextlib import redirect_stdout

from helpers import GOOD, FakeGitHub, issue_body, issue_event
import readme
import submission
from issue_forms import form_fields, parse
from trust import assess, label_applied_by_maintainer

TEXT = readme.read()
POLICY = submission.load_policy()


def evaluate(**overrides):
    return submission.evaluate(parse(issue_body(**overrides), submission.TEMPLATE), POLICY, TEXT)


def errors(**overrides):
    return " ".join(evaluate(**overrides).errors)


class TemplateSyncTest(unittest.TestCase):
    fields = {f.id: f for f in form_fields(submission.TEMPLATE)}

    def test_option_maps_match_template(self):
        self.assertEqual(self.fields["protocols"].options, list(submission.PROTOCOLS))
        self.assertEqual(self.fields["platforms"].options, list(submission.PLATFORMS))
        self.assertEqual(self.fields["payment"].options, list(submission.PAYMENT))
        self.assertEqual(self.fields["affiliation"].options, list(submission.AFFILIATION))

    def test_every_field_is_used(self):
        used = set(GOOD) | {"confirmations"}
        self.assertEqual(set(self.fields), used)

    def test_template_label_is_submission(self):
        self.assertIn('labels: ["submission"]', submission.TEMPLATE.read_text(encoding="utf-8"))


class ParsingTest(unittest.TestCase):
    def test_round_trip(self):
        form = parse(issue_body(), submission.TEMPLATE)
        self.assertEqual(form["name"], "Example VPN")
        self.assertTrue(form["protocols"]["VLESS Reality (XRay)"])
        self.assertFalse(form["protocols"]["AmneziaWG"])
        self.assertTrue(all(form["confirmations"].values()))

    def test_injected_heading_cannot_overwrite_earlier_field(self):
        body = issue_body(notes="Текст достаточной длины для заметки.\n### Официальный сайт\n\nhttps://evil.example/")
        self.assertEqual(parse(body, submission.TEMPLATE)["url"], "https://example-vpn.com/")


class EvaluateTest(unittest.TestCase):
    def test_good_submission(self):
        out = evaluate()
        self.assertEqual(out.errors, [])
        svc = out.service
        self.assertEqual(svc["price_month"], "200₽")
        self.assertEqual(svc["telegram"], "examplevpn_bot")
        self.assertEqual(svc["protocols"], ["VLESS Reality", "Hysteria2/TUIC"])
        self.assertEqual(out.labels, set())
        readme.insert_service(TEXT, svc)  # не бросает

    def test_requires_obfuscation(self):
        self.assertIn("обфускацией", errors(protocols=["WireGuard без обфускации", "OpenVPN без обфускации"]))

    def test_marketing_and_links(self):
        self.assertIn("рекламную", errors(summary="Лучший VPN для России."))
        self.assertIn("ссылок", errors(about="Подробнее на https://example-vpn.com/about."))
        self.assertIn("`@`", errors(summary="Пишите @examplevpn_bot."))

    def test_url_hygiene(self):
        self.assertIn("utm_source", errors(url="https://example-vpn.com/?utm_source=github"))
        self.assertIn("Политика конфиденциальности", errors(privacy_url=""))
        self.assertIn("Страница с ценами", errors(pricing_url=""))
        self.assertEqual(errors(price_month="Бесплатно", price_year="", pricing_url="",
                                payment=["Сервис полностью бесплатный"]), "")

    def test_duplicates(self):
        self.assertIn("уже есть в списке", errors(name="Blanc VPN"))
        self.assertIn("уже упоминается", errors(name="Другой", url="https://www.blancvpn.pro/en"))
        self.assertIn("уже упоминается", errors(name="Другой", url="https://t.me/rosvpnbot"))

    def test_lookalikes_are_flagged(self):
        out = evaluate(name="BlancVPN Pro", url="https://blanc-vpn.pro/", pricing_url="https://blanc-vpn.pro/p",
                       privacy_url="https://blanc-vpn.pro/privacy")
        self.assertEqual(out.errors, [])
        self.assertTrue(any("похоже" in w for w in out.warnings))
        self.assertTrue(any("фишинговая" in w for w in out.warnings))

    def test_name_rules(self):
        self.assertIn("смешаны", errors(name="ВlancVPN"))  # первая «В» кириллическая
        self.assertIn("40 символов", errors(name="Example | VPN"))

    def test_prices(self):
        self.assertIn("не понимаю", errors(price_month="двести рублей"))
        for price in ("$5", "€7.99", "2390₽ / 6 мес", "490 ₽"):
            self.assertEqual(errors(price_month=price), "", price)

    def test_vendor_and_confirmations(self):
        out = evaluate(affiliation="Я владелец или сотрудник сервиса, либо он попросил меня")
        self.assertIn("vendor-submitted", out.labels)
        self.assertIn("Подтверждениях", errors(confirmations=[]))
        self.assertIn("подробнее", errors(notes="Работает."))


class RunTest(unittest.TestCase):
    def run_bot(self, event):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            submission.run(event, None, online=False, dry_run=True)
        return buffer.getvalue()

    def test_offline_dry_run(self):
        output = self.run_bot(issue_event())
        self.assertIn("Проверки пройдены", output)
        self.assertIn("Closes #42", output)
        self.assertNotIn("@examplevpn_bot", output)  # упоминания обезврежены

    def test_ignores_other_issues(self):
        self.assertIn("не заявка", self.run_bot(issue_event(labels=("feedback",))))

    def test_errors_are_reported(self):
        self.assertIn("Нужно исправить", self.run_bot(issue_event(summary="Лучший VPN.")))


class TrustTest(unittest.TestCase):
    def test_young_account_rejected(self):
        result = assess(FakeGitHub(created_days_ago=5), POLICY, "x", "NONE", kind="new_service")
        self.assertFalse(result.ok)

    def test_young_account_with_merged_pr_allowed(self):
        result = assess(FakeGitHub(created_days_ago=5, merged=1), POLICY, "x", "NONE", kind="new_service")
        self.assertTrue(result.ok)

    def test_limits(self):
        self.assertFalse(assess(FakeGitHub(open_items=[7]), POLICY, "x", "NONE", kind="new_service",
                                exclude_issue=42).ok)
        self.assertTrue(assess(FakeGitHub(open_items=[42]), POLICY, "x", "NONE", kind="new_service",
                               exclude_issue=42).ok)
        self.assertFalse(assess(FakeGitHub(recent=4), POLICY, "x", "NONE", kind="new_service").ok)

    def test_bots_and_orgs_rejected(self):
        self.assertFalse(assess(FakeGitHub(user_type="Organization"), POLICY, "x", "NONE", kind="new_service").ok)

    def test_maintainer_skips_checks(self):
        result = assess(FakeGitHub(created_days_ago=1, permissions={"owner": "admin"}), POLICY, "owner", "NONE",
                        kind="new_service")
        self.assertTrue(result.ok and result.maintainer)

    def test_soft_flag(self):
        result = assess(FakeGitHub(created_days_ago=60, repos=0), POLICY, "x", "NONE", kind="new_service")
        self.assertTrue(result.ok)
        self.assertTrue(result.flags)

    def test_override_label_must_come_from_maintainer(self):
        def labeled(actor):
            return {"event": "labeled", "label": {"name": "maintainer-approved"}, "actor": {"login": actor}}

        by_author = FakeGitHub(events=[labeled("author")], permissions={"owner": "admin"})
        self.assertFalse(label_applied_by_maintainer(by_author, 1, "maintainer-approved"))
        by_owner = FakeGitHub(events=[labeled("author"), labeled("owner")], permissions={"owner": "admin"})
        self.assertTrue(label_applied_by_maintainer(by_owner, 1, "maintainer-approved"))


if __name__ == "__main__":
    unittest.main()
