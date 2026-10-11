import io
import unittest
from contextlib import redirect_stdout

import helpers  # noqa: F401  (добавляет scripts/ в sys.path)
import readme
import validate

TEXT = readme.read()
SERVICE = {
    "name": "Example VPN", "url": "https://example-vpn.com/", "telegram": "examplevpn_bot",
    "summary": "Тестовый сервис.", "about": "Абзац о сервисе.", "protocols": ["VLESS Reality"],
    "platforms": ["Android"], "free": "", "payment": ["криптовалюта"], "price_month": "200₽",
    "price_year": "—", "pricing_url": "", "privacy_url": "https://example-vpn.com/privacy", "repo": "",
}


def validation_errors(text: str) -> list[str]:
    report = validate.Report()
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        validate.check_structure(text, report)
        validate.check_duplicates(text, report)
        validate.check_links(text, report)
    return [line for line in buffer.getvalue().splitlines() if "error" in line.lower()]


class AnchorTest(unittest.TestCase):
    def test_matches_github(self):
        # Сверено с id заголовков, которые GitHub выдал для этого README.
        cases = {
            "🛡️ Как работают блокировки: DPI и ТСПУ": "️-как-работают-блокировки-dpi-и-тспу",
            "📊 Сравнительная таблица VPN-сервисов для России": "-сравнительная-таблица-vpn-сервисов-для-россии",
            "Vepen.online — от телеканала Дождь": "vepenonline--от-телеканала-дождь",
            "Outline VPN — Jigsaw/Google": "outline-vpn--jigsawgoogle",
            "❓ Часто задаваемые вопросы (FAQ)": "-часто-задаваемые-вопросы-faq",
        }
        for heading, anchor in cases.items():
            self.assertEqual(readme.github_anchor(heading), anchor)

    def test_duplicate_headings_get_suffix(self):
        anchors = [a for _, _, a in readme.headings("## A\n\n## A\n\n```\n## A\n```\n## A")]
        self.assertEqual(anchors, ["a", "a-1", "a-2"])


class CurrentReadmeTest(unittest.TestCase):
    def test_readme_is_valid(self):
        self.assertEqual(validation_errors(TEXT), [])

    def test_main_table_services(self):
        main = [s.name for s in readme.services(TEXT) if s.in_main_table]
        self.assertIn("BlancVPN", main)
        self.assertIn("Orden VPN", main)
        self.assertNotIn("@rosvpnbot", main)


class InsertTest(unittest.TestCase):
    def setUp(self):
        self.text = readme.insert_service(TEXT, SERVICE)
        self.lines = self.text.split("\n")

    def test_still_valid(self):
        self.assertEqual(validation_errors(self.text), [])

    def test_row_is_last_in_main_table(self):
        end = readme.marker_index(self.lines, readme.SERVICES_END)
        rows = [line for line in self.lines[:end] if line.startswith("| **[")]
        self.assertTrue(rows[-1].startswith("| **[Example VPN](https://example-vpn.com/)** | Тестовый сервис. |"))
        self.assertEqual(self.lines[end - 1], "")

    def test_review_before_marker(self):
        end = readme.marker_index(self.lines, readme.REVIEWS_END)
        block = "\n".join(self.lines[:end])
        self.assertTrue(block.rstrip().endswith("---"))
        self.assertEqual(readme.review_headings(self.text)[-1], "Example VPN")
        self.assertIn("**Telegram:** [@examplevpn_bot](https://t.me/examplevpn_bot)", block)
        self.assertIn("- **Цены:** 200₽ в месяц\n", block)

    def test_toc_item_after_last_review(self):
        i = self.lines.index("  - [Example VPN](#example-vpn)")
        self.assertEqual(self.lines[i - 1], "  - [The Ins VPN](#the-ins-vpn)")
        self.assertTrue(self.lines[i + 1].startswith("- [Telegram-боты"))

    def test_broken_markup_raises(self):
        with self.assertRaises(ValueError):
            readme.insert_service(TEXT.replace("<!-- reviews:end", "<!-- removed"), SERVICE)

    def test_broken_markup_is_reported_not_crashed(self):
        text = TEXT.replace(readme.REVIEWS_HEADING, "Обзоры")
        self.assertTrue(any("reviews:end" in e for e in validation_errors(text)))


class PriceTest(unittest.TestCase):
    def test_phrases(self):
        self.assertEqual(readme.price_phrase("200₽", "1920₽"), "200₽ в месяц, 1920₽ в год")
        self.assertEqual(readme.price_phrase("2390₽ / 6 мес", "3899₽"), "2390₽ за 6 мес., 3899₽ в год")
        self.assertEqual(readme.price_phrase("$5", "—"), "$5 в месяц")
        self.assertEqual(readme.price_phrase("Бесплатно", "Бесплатно"), "бесплатно")


if __name__ == "__main__":
    unittest.main()
