from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from herald.weibo_sample_collector import _resolve_date_window, _resolve_max_pages


UTC = timezone.utc
CHINA_TIME = timezone(timedelta(hours=8))


class WeiboSampleCollectorDateWindowTests(unittest.TestCase):
    def test_explicit_date_range_is_inclusive_in_china_time(self) -> None:
        published_from, published_before = _resolve_date_window(
            SimpleNamespace(
                start_date=date(2026, 8, 1),
                end_date=date(2026, 8, 31),
                lookback_days=45,
            ),
            now=datetime(2026, 9, 1, 2, tzinfo=UTC),
        )

        self.assertEqual(
            published_from,
            datetime(2026, 8, 1, tzinfo=CHINA_TIME),
        )
        self.assertEqual(
            published_before,
            datetime(2026, 9, 1, tzinfo=CHINA_TIME),
        )

    def test_date_range_requires_both_bounds(self) -> None:
        with self.assertRaisesRegex(ValueError, "together"):
            _resolve_date_window(
                SimpleNamespace(
                    start_date=date(2026, 8, 1),
                    end_date=None,
                    lookback_days=45,
                ),
                now=datetime(2026, 9, 1, 2, tzinfo=UTC),
            )

    def test_date_range_rejects_end_before_start(self) -> None:
        with self.assertRaisesRegex(ValueError, "on or after"):
            _resolve_date_window(
                SimpleNamespace(
                    start_date=date(2026, 8, 31),
                    end_date=date(2026, 8, 1),
                    lookback_days=45,
                ),
                now=datetime(2026, 9, 1, 2, tzinfo=UTC),
            )

    def test_lookback_days_cover_china_natural_dates_including_today(self) -> None:
        now = datetime(2026, 9, 1, 2, tzinfo=UTC)
        published_from, published_before = _resolve_date_window(
            SimpleNamespace(start_date=None, end_date=None, lookback_days=45),
            now=now,
        )

        self.assertEqual(
            published_from,
            datetime(2026, 7, 19, tzinfo=CHINA_TIME),
        )
        self.assertEqual(
            published_before,
            datetime(2026, 9, 2, tzinfo=CHINA_TIME),
        )

    def test_default_page_limit_is_two_pages_per_natural_day(self) -> None:
        max_pages = _resolve_max_pages(
            SimpleNamespace(max_pages=None),
            published_from=datetime(2026, 8, 1, tzinfo=CHINA_TIME),
            published_before=datetime(2026, 9, 1, tzinfo=CHINA_TIME),
        )

        self.assertEqual(max_pages, 62)

    def test_explicit_page_limit_overrides_date_based_default(self) -> None:
        max_pages = _resolve_max_pages(
            SimpleNamespace(max_pages=12),
            published_from=datetime(2026, 8, 1, tzinfo=CHINA_TIME),
            published_before=datetime(2026, 9, 1, tzinfo=CHINA_TIME),
        )

        self.assertEqual(max_pages, 12)


if __name__ == "__main__":
    unittest.main()
