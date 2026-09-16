import unittest
from datetime import datetime, timezone

from herald.models import Activity, ActivityKind, EventAction, ActionKind, Campaign
from herald.scheduler import ScheduleCompiler


class ParticipationTests(unittest.TestCase):
    def test_card_moves_to_next_offer_and_keeps_unknown_activity(self):
        from herald.site import StaticSiteBuilder
        now = datetime(2026, 9, 12, tzinfo=timezone.utc)
        activity = Activity(id='a', kind='merchandise', title='预售', actions=[
            EventAction(id='sale', kind='sale_open', title='预售购买'),
            EventAction(id='discount', kind='discount', title='折扣', end_at='2026-09-14T23:59:00+08:00'),
            EventAction(id='gift', kind='gift', title='满赠', end_at='2026-09-21T23:59:00+08:00')])
        campaign = Campaign(id='c', ip_slug='arknights', ip_name='明日方舟', title='企划',
                            first_seen_at=now, updated_at=now, activities=[activity])
        builder = StaticSiteBuilder()
        self.assertEqual(builder._activity_cards([campaign], now)[0]['next']['title'], '折扣截止')
        self.assertEqual(builder._activity_cards([campaign], datetime(2026, 9, 15, tzinfo=timezone.utc))[0]['next']['title'], '满赠截止')
        self.assertIsNone(builder._activity_cards([campaign], datetime(2026, 10, 1, tzinfo=timezone.utc))[0]['next'])

    def test_extracted_constraints_survive_schema(self):
        from herald.ai import ExtractedAction
        action = ExtractedAction(kind='gift', title='满赠', scope='指定商品',
                                 quantity_limit='500套', end_condition='赠完即止', ended=False)
        stored = EventAction(id='x', **action.model_dump())
        self.assertEqual(stored.quantity_limit, '500套')

    def test_offer_windows_and_unknown_sale(self):
        now = datetime(2026, 9, 12, tzinfo=timezone.utc)
        activity = Activity(id='a', kind=ActivityKind.MERCHANDISE, title='预售', actions=[
            EventAction(id='sale', kind=ActionKind.SALE_OPEN, title='购买'),
            EventAction(id='gift', kind=ActionKind.GIFT, title='满赠',
                        end_at='2026-09-21T23:59:00+08:00', end_condition='赠完即止', quantity_limit='限量500套'),
            EventAction(id='discount', kind=ActionKind.DISCOUNT, title='全店折扣',
                        end_at='2026-09-14T23:59:00+08:00', scope='全店，部分除外'),
        ])
        campaign = Campaign(id='c', ip_slug='arknights', ip_name='明日方舟', title='企划',
                            first_seen_at=now, updated_at=now, activities=[activity])
        jobs = ScheduleCompiler().future_jobs(campaign, now)
        self.assertEqual({str(j.due_date) for j in jobs}, {'2026-09-13', '2026-09-20'})
        self.assertTrue(all(ScheduleCompiler.validate_due_job(j, campaign) for j in jobs))
        self.assertTrue(activity.is_visible(datetime(2026, 10, 1, tzinfo=timezone.utc)))

    def test_conditional_end_does_not_create_deadline(self):
        now = datetime(2026, 9, 12, tzinfo=timezone.utc)
        activity = Activity(id='a', kind='merchandise', title='限量购买', actions=[
            EventAction(id='x', kind='gift', title='满赠', end_condition='赠完即止')])
        from herald.scheduler import schedule_actions
        self.assertEqual(schedule_actions(activity, timezone.utc), [])

    def test_ended_action_has_no_future_schedule(self):
        from herald.scheduler import schedule_actions
        activity = Activity(id='a', kind='merchandise', title='购买', actions=[
            EventAction(id='x', kind='discount', title='优惠', at='2026-10-01T12:00:00+08:00', ended=True)])
        self.assertEqual(schedule_actions(activity, timezone.utc), [])
