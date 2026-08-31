from __future__ import annotations

import unittest

from herald.ai import (
    ExtractedAction,
    ExtractedActivity,
    ExtractedClaim,
    ExtractedVenue,
    ExtractionResult,
)
from herald.ai_smoke import (
    evaluate_historical_result,
    historical_collaboration_packet,
)
from herald.models import ActionKind, ActivityKind


def extracted_result(*, first_action_at: str | None = None) -> ExtractionResult:
    return ExtractionResult(
        relevant=True,
        campaign_title="原神 × 美团丨大众点评",
        partner="美团丨大众点评",
        activities=[
            ExtractedActivity(
                kind=ActivityKind.POPUP,
                title="美团快闪预约",
                actions=[
                    ExtractedAction(
                        kind=ActionKind.RESERVATION_OPEN,
                        title="美团快闪预约开启",
                        at=first_action_at,
                    )
                ],
            ),
            ExtractedActivity(
                kind=ActivityKind.ONLINE,
                title="大众点评打卡活动",
                actions=[
                    ExtractedAction(
                        kind=ActionKind.EVENT_START,
                        title="大众点评打卡活动开启",
                    )
                ],
            ),
            ExtractedActivity(
                kind=ActivityKind.ONLINE,
                title="美团原神专属会场",
                actions=[
                    ExtractedAction(
                        kind=ActionKind.EVENT_START,
                        title="美团原神专属会场开启",
                    )
                ],
            ),
        ],
        claims=[
            ExtractedClaim(
                field_path="partner",
                quote="原神 × 美团丨大众点评联名活动",
                confidence=1,
            )
        ],
        uncertainties=["公告只给出日期，未给出三个节点的具体时刻。"],
    )


class HistoricalAiSmokeTests(unittest.TestCase):
    def test_expected_extraction_passes_quality_checks(self) -> None:
        checks = evaluate_historical_result(
            historical_collaboration_packet(), extracted_result()
        )

        self.assertTrue(all(checks.values()), checks)

    def test_invented_midnight_fails_conservative_time_check(self) -> None:
        checks = evaluate_historical_result(
            historical_collaboration_packet(),
            extracted_result(first_action_at="2026-08-16T00:00:00+08:00"),
        )

        self.assertFalse(checks["no_invented_times"])

    def test_missing_end_time_does_not_cover_missing_time_of_day(self) -> None:
        result = extracted_result()
        result.uncertainties = ["三个活动的结束时间未披露。"]

        checks = evaluate_historical_result(
            historical_collaboration_packet(), result
        )

        self.assertFalse(checks["missing_time_of_day_reported"])

    def test_invented_venue_and_action_url_fail_grounding_checks(self) -> None:
        result = extracted_result()
        result.activities[0].venues = [
            ExtractedVenue(name="美团平台", nationwide=True)
        ]
        result.activities[0].actions[0].url = (
            "https://weibo.com/6593199887/RdDIG4Sea"
        )

        checks = evaluate_historical_result(
            historical_collaboration_packet(), result
        )

        self.assertFalse(checks["no_invented_venues"])
        self.assertFalse(checks["no_invented_action_urls"])


if __name__ == "__main__":
    unittest.main()
