from __future__ import annotations

import hashlib
import unittest
from datetime import datetime, timezone

from herald.candidates import CandidateFilter
from herald.models import SourceKind, SourceObservation, SourceRef


UTC = timezone.utc
NOW = datetime(2026, 8, 30, 12, tzinfo=UTC)


def observation(text: str, *, media_text: list[str] | None = None) -> SourceObservation:
    return SourceObservation(
        id="weibo-a",
        source=SourceRef(
            id="weibo-a",
            kind=SourceKind.WEIBO,
            url="https://weibo.com/1/a",
            account_name="官方账号",
            published_at=NOW,
            first_seen_at=NOW,
            content_hash=hashlib.sha256(text.encode()).hexdigest(),
        ),
        text=text,
        extracted_media_text=media_text or [],
    )


class CandidateFilterTests(unittest.TestCase):
    def test_offline_derivative_events_do_not_require_collaboration(self):
        for term in ('漫展', '嘉年华', '演唱会', '音乐会', '巡展', '线下活动', '主题展'):
            with self.subTest(term=term):
                decision = CandidateFilter().evaluate(observation(f'官方{term}门票即将开售'),
                    ip_names=['明日方舟'], from_official_ip_account=True)
                self.assertTrue(decision.relevant)
                self.assertIn(term, decision.matched_terms)

    def test_carnival_merchandise_is_a_candidate(self):
        decision = CandidateFilter().evaluate(
            observation('▼明日方舟：终末地 ／ 2026鹰角嘉年华 主题周边▼现场&线上 贩售情报公开！'),
            ip_names=['明日方舟：终末地'], from_official_ip_account=True)
        self.assertTrue(decision.relevant)

    def setUp(self) -> None:
        self.filter = CandidateFilter()

    def test_official_ip_post_with_collaboration_term_is_relevant(self) -> None:
        decision = self.filter.evaluate(
            observation("全新品牌联动即将开启"),
            ip_names=["原神"],
            from_official_ip_account=True,
        )

        self.assertTrue(decision.relevant)
        self.assertIn("联动", decision.matched_terms)

    def test_official_ip_cooperation_clothing_post_is_relevant(self) -> None:
        decision = self.filter.evaluate(
            observation("【新增服饰】虎狼丸，女神异闻录3 Reload合作服装。"),
            ip_names=["明日方舟", "Arknights"],
            from_official_ip_account=True,
        )

        self.assertTrue(decision.relevant)
        self.assertIn("合作", decision.matched_terms)

    def test_national_library_merchandise_without_collaboration_keyword(self) -> None:
        decision = self.filter.evaluate(
            observation(
                "〓朝陇山26 Oct.｜余音向海〓上新\n"
                "【满赠特典】\n"
                "单笔订单下单“余音向海、明日方舟×国家图书馆「恒远津梁」系列”"
                "任意商品，即赠 特典明信片-昨日海 一张。"
            ),
            ip_names=["明日方舟", "Arknights", "方舟"],
            from_official_ip_account=True,
        )

        self.assertTrue(decision.relevant)
        self.assertIn("明日方舟×国家图书馆", decision.matched_terms)

    def test_named_crossover_supports_wrappers_aliases_and_both_orders(self) -> None:
        for text in (
            "《明日方舟》 × @国家图书馆「恒远津梁」系列上新",
            "【国家图书馆】×《明日方舟》系列上新",
            "明日方舟x国家图书馆「恒远津梁」系列上新",
            "国家图书馆X明日方舟「恒远津梁」系列上新",
            "Arknights × National Library of China",
            "Sanrio x ARKNIGHTS",
            "《明日方舟》 X 《女神异闻录3 Reload》系列周边",
        ):
            with self.subTest(text=text):
                decision = self.filter.evaluate(
                    observation(text),
                    ip_names=["明日方舟", "Arknights"],
                    from_official_ip_account=True,
                )
                self.assertTrue(decision.relevant)

    def test_named_crossover_must_pair_the_configured_ip(self) -> None:
        for official in (False, True):
            with self.subTest(official=official):
                paired = self.filter.evaluate(
                    observation("国家图书馆×明日方舟「恒远津梁」系列上新"),
                    ip_names=["明日方舟"],
                    from_official_ip_account=official,
                )
                unrelated = self.filter.evaluate(
                    observation("原神版本更新。国家图书馆×明日方舟「恒远津梁」系列上新"),
                    ip_names=["原神"],
                    from_official_ip_account=official,
                )
                self.assertTrue(paired.relevant)
                self.assertFalse(unrelated.relevant)

    def test_quantity_and_incomplete_pairs_are_not_crossover_signals(self) -> None:
        for text in (
            "明日方舟维护补偿：合成玉×800，龙门币×10000。",
            "明日方舟×800份",
            "800份×明日方舟",
            "Arknights x 2",
            "2 x Arknights",
            "明日方舟×",
            "×明日方舟",
            "明日方舟×\n国家图书馆",
            "Arknights Xbox版本预告",
            "NotArknights × National Library of China",
            "National Library of China × ArknightsExtra",
            "明日方舟新版本预约，画布尺寸1920×1080。",
        ):
            with self.subTest(text=text):
                decision = self.filter.evaluate(
                    observation(text),
                    ip_names=["明日方舟", "Arknights"],
                    from_official_ip_account=True,
                )
                self.assertFalse(decision.relevant)

    def test_non_official_source_must_explicitly_name_the_ip(self) -> None:
        without_ip = self.filter.evaluate(
            observation("全新联动即将开启"),
            ip_names=["原神"],
            from_official_ip_account=False,
        )
        with_ip = self.filter.evaluate(
            observation("原神全新联动即将开启"),
            ip_names=["原神"],
            from_official_ip_account=False,
        )

        self.assertFalse(without_ip.relevant)
        self.assertTrue(with_ip.relevant)

    def test_action_word_without_collaboration_signal_is_not_enough(self) -> None:
        decision = self.filter.evaluate(
            observation("新版本预约现已开放"),
            ip_names=["原神"],
            from_official_ip_account=True,
        )

        self.assertFalse(decision.relevant)

    def test_poster_ocr_text_participates_in_filtering(self) -> None:
        decision = self.filter.evaluate(
            observation("详情见图", media_text=["原神主题快闪店 9月8日开放预约"]),
            ip_names=["原神"],
            from_official_ip_account=True,
        )

        self.assertTrue(decision.relevant)
        self.assertIn("快闪", decision.matched_terms)
        self.assertIn("预约", decision.matched_terms)


if __name__ == "__main__":
    unittest.main()
