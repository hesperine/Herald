from __future__ import annotations

import unittest

from herald.config import load_settings


class LoadSettingsTests(unittest.TestCase):
    def test_parses_public_and_private_settings(self) -> None:
        settings = load_settings(
            {
                "WATCH_IPS": "原神, 明日方舟\n原神",
                "COUNTRY": "cn",
                "EXTRA_WEIBO_UIDS": "原神:1001,1002\n明日方舟:2001",
                "INCLUDE_IN_GAME": "true",
                "REMIND_DAY_BEFORE": "false",
                "ORIGIN_CITY": "上海",
                "NOTIFY_EMAIL": "player@example.com",
                "SMTP_HOST": "smtp.example.com",
                "SMTP_PORT": "587",
                "SMTP_USE_SSL": "false",
                "AI_VISION": "true",
                "PUBLISH_REACHABILITY": "true",
                "AI_JSON_MODE": "false",
            }
        )

        self.assertEqual(settings.public.watched_ips, ["原神", "明日方舟", "原神"])
        self.assertEqual(settings.public.country, "CN")
        self.assertEqual(settings.public.extra_weibo_uids["原神"], ["1001", "1002"])
        self.assertTrue(settings.public.include_in_game)
        self.assertFalse(settings.public.remind_day_before)
        self.assertEqual(settings.public.smtp_host, "smtp.example.com")
        self.assertEqual(settings.public.smtp_port, 587)
        self.assertFalse(settings.public.smtp_use_ssl)
        self.assertTrue(settings.public.ai_vision)
        self.assertTrue(settings.public.publish_reachability)
        self.assertFalse(settings.public.ai_json_mode)
        self.assertEqual(settings.private.origin_city.get_secret_value(), "上海")
        self.assertEqual(
            settings.private.notify_email.get_secret_value(), "player@example.com"
        )

    def test_requires_at_least_one_ip(self) -> None:
        with self.assertRaisesRegex(ValueError, "WATCH_IPS"):
            load_settings({})

    def test_rejects_invalid_extra_uid_format(self) -> None:
        with self.assertRaisesRegex(ValueError, "IP:uid"):
            load_settings({"WATCH_IPS": "原神", "EXTRA_WEIBO_UIDS": "1001"})

        with self.assertRaisesRegex(ValueError, "numeric"):
            load_settings(
                {"WATCH_IPS": "原神", "EXTRA_WEIBO_UIDS": "原神:not-a-uid"}
            )

    def test_first_release_rejects_non_china_country(self) -> None:
        with self.assertRaisesRegex(ValueError, "COUNTRY=CN"):
            load_settings({"WATCH_IPS": "原神", "COUNTRY": "JP"})

    def test_fingerprint_does_not_contain_secret_values(self) -> None:
        settings = load_settings(
            {
                "WATCH_IPS": "原神",
                "AI_API_KEY": "top-secret-key",
                "NOTIFY_EMAIL": "private@example.com",
            }
        )

        fingerprint = settings.profile_fingerprint()

        self.assertEqual(len(fingerprint), 64)
        self.assertNotIn("top-secret-key", fingerprint)
        self.assertNotIn("private@example.com", fingerprint)


if __name__ == "__main__":
    unittest.main()
