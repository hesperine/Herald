from __future__ import annotations

import unittest

from herald.config import PublicSettings
from herald.registry import IpRegistry


class IpRegistryTests(unittest.TestCase):
    def test_builtin_ips_include_their_official_weibo_sources(self) -> None:
        resolution = IpRegistry.load_builtin().resolve(
            PublicSettings(watched_ips=["原神", "明日方舟"])
        )

        sources = {
            item.slug: [source.account_id for source in item.sources]
            for item in resolution.supported
        }
        self.assertEqual(sources["genshin-impact"], ["6593199887"])
        self.assertEqual(sources["arknights"], ["6279793937"])

    def setUp(self) -> None:
        self.registry = IpRegistry.load_builtin()

    def test_resolves_name_alias_and_deduplicates_slug(self) -> None:
        settings = PublicSettings(watched_ips=["原神", "Genshin", "Arknights"])

        resolution = self.registry.resolve(settings)

        self.assertEqual(
            [entry.slug for entry in resolution.supported],
            ["genshin-impact", "arknights"],
        )
        self.assertEqual(resolution.unsupported, [])

    def test_reports_unknown_ip_without_auto_discovery(self) -> None:
        settings = PublicSettings(watched_ips=["不存在的IP"])

        resolution = self.registry.resolve(settings)

        self.assertEqual(resolution.supported, [])
        self.assertEqual(resolution.unsupported, ["不存在的IP"])

    def test_extra_uid_only_extends_a_watched_builtin_ip(self) -> None:
        settings = PublicSettings(
            watched_ips=["原神"],
            extra_weibo_uids={
                "原神": ["1001", "1001"],
                "未知IP": ["9999"],
            },
        )

        resolution = self.registry.resolve(settings)

        self.assertEqual(resolution.unsupported, ["未知IP"])
        sources = resolution.supported[0].sources
        self.assertEqual(
            [source.account_id for source in sources], ["6593199887", "1001"]
        )


if __name__ == "__main__":
    unittest.main()
