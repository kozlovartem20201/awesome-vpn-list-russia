import unittest

import helpers  # noqa: F401
from netcheck import FetchResult
from policy import identity_key, style_problems, text_problems, url_problems


class UrlTest(unittest.TestCase):
    def test_clean(self):
        for url in ("https://blancvpn.pro/ru", "https://t.me/rosvpnbot", "https://vpnpay.io/ru/libra/",
                    "https://s3.amazonaws.com/ural-freedom/index.html"):
            self.assertEqual(url_problems(url), [], url)

    def test_rejected(self):
        cases = {
            "http://example.com/": "https://",
            "https://example.com/?utm_source=x": "utm_source",
            "https://example.com/?ref=abc": "`ref`",
            "https://t.me/examplebot?start=ref123": "`start`",
            "https://example.com/ref/abc": "реферальные",
            "https://bit.ly/abc": "сокращатели",
            "https://clck.ru/abc": "сокращатели",
            "https://1.2.3.4/": "IP-адрес",
            "https://xn--e1afmkfd.xn--p1ai/": "punycode",
            "https://пример.рф/": "punycode",
            "https://example.com:8443/": "порт",
            "https://user:pass@example.com/": "логин",
            "https://exa​mple.com/": "невидимые",
        }
        for url, needle in cases.items():
            self.assertTrue(any(needle in p for p in url_problems(url)), (url, url_problems(url)))

    def test_identity(self):
        self.assertEqual(identity_key("https://www.blancvpn.pro/ru"), "blancvpn.pro")
        self.assertEqual(identity_key("https://t.me/RosVpnBot"), "t.me/rosvpnbot")
        self.assertEqual(identity_key("https://github.com/a/b/tree/main"), "github.com/a/b")
        self.assertEqual(identity_key("https://s3.amazonaws.com/ural-freedom/index.html"),
                         "s3.amazonaws.com/ural-freedom")
        self.assertEqual(identity_key("https://vpn.theins.ru/"), "theins.ru")


class MovedDomainTest(unittest.TestCase):
    def test_same_site_redirect_is_not_a_move(self):
        login = FetchResult(url="https://github.com/o/r/issues/new", status=200,
                            final_url="https://github.com/login?return_to=x")
        self.assertFalse(login.moved_domain)
        self.assertFalse(FetchResult(url="https://www.example.com/", status=200,
                                     final_url="https://example.com/ru/").moved_domain)

    def test_other_domain_is_a_move(self):
        self.assertTrue(FetchResult(url="https://joinorden.com", status=200,
                                    final_url="https://internet-gid.com/").moved_domain)


class TextTest(unittest.TestCase):
    def test_structure(self):
        cases = {
            "Текст | с трубой.": "`|`",
            "Смотрите https://x.com.": "ссылок",
            "Пишите @owner.": "`@`",
            "<b>Жирный</b>.": "HTML",
            "[ссылка] без адреса.": "Markdown",
            "# Заголовок.": "Markdown",
            "Длинно." * 100: "длиннее",
        }
        for text, needle in cases.items():
            self.assertTrue(any(needle in p for p in text_problems(text, max_length=200)), (text, needle))
        self.assertEqual(text_problems("50+ локаций, оплата картой МИР.", max_length=200), [])

    def test_style(self):
        self.assertTrue(style_problems("Лучший VPN в России."))
        self.assertTrue(style_problems("Самый быстрый сервис."))
        self.assertTrue(style_problems("Работает на 100%."))
        self.assertTrue(style_problems("Без точки"))
        self.assertTrue(style_problems("Подключайтесь!"))
        self.assertEqual(style_problems("Улучшенный протокол, 20 стран."), [])  # «улучш» — не «лучш»
        self.assertEqual(style_problems("Один из самых старых сервисов."), [])


if __name__ == "__main__":
    unittest.main()
