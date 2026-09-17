from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from herald.models import (
    ActionKind,
    Activity,
    ActivityKind,
    Campaign,
    EventAction,
    EventStatus,
    NotificationKind,
    QueueJob,
)
from herald.notifications import NotificationService, SmtpEmailSender
from herald.storage import StateStore


UTC = timezone.utc
NOW = datetime(2026, 9, 7, 13, tzinfo=UTC)
DUE_DAY = date(2026, 9, 7)
ACTION_AT = datetime(2026, 9, 8, 2, tzinfo=UTC)


class SmtpRecipientTests(unittest.TestCase):
    def sender(self, use_ssl=True):
        return SmtpEmailSender(host='smtp.example.com', port=465 if use_ssl else 587,
                               username='sender@example.com', password='test-only', use_ssl=use_ssl)

    def test_multiple_recipients_with_ssl_and_starttls(self):
        for use_ssl in (True, False):
            with self.subTest(use_ssl=use_ssl), patch('herald.notifications.smtplib.SMTP_SSL' if use_ssl else 'herald.notifications.smtplib.SMTP') as smtp:
                client=smtp.return_value.__enter__.return_value
                client.send_message.return_value={}
                self.sender(use_ssl).send(recipient='one@example.com, two@example.com, one@example.com', subject='提醒', text='正文')
                args, kwargs=client.send_message.call_args
                self.assertEqual(kwargs['to_addrs'], ['one@example.com', 'two@example.com'])
                self.assertNotIn('one@example.com', str(args[0]['To']))
                self.assertNotIn('two@example.com', str(args[0]['To']))
                if not use_ssl: client.starttls.assert_called_once()

    def test_single_recipient_remains_supported(self):
        with patch('herald.notifications.smtplib.SMTP_SSL') as smtp:
            client=smtp.return_value.__enter__.return_value
            client.send_message.return_value={}
            self.sender().send(recipient='one@example.com', subject='提醒', text='正文')
            args, kwargs=client.send_message.call_args
            self.assertEqual(str(args[0]['To']), 'one@example.com')
            self.assertEqual(kwargs['to_addrs'], ['one@example.com'])

    def test_invalid_list_rejected_before_connecting_without_exposing_address(self):
        for value in (' , ', 'one@example.com,invalid', 'one@example.com,broken@@example.com', 'one@example.com\nBcc: other@example.com'):
            with self.subTest(value=value), patch('herald.notifications.smtplib.SMTP_SSL') as smtp:
                with self.assertRaisesRegex(ValueError, '^invalid NOTIFY_EMAIL recipient list$'):
                    self.sender().send(recipient=value, subject='提醒', text='正文')
                smtp.assert_not_called()

    def test_partial_refusal_is_not_reported_as_success(self):
        with patch('herald.notifications.smtplib.SMTP_SSL') as smtp:
            smtp.return_value.__enter__.return_value.send_message.return_value={'two@example.com': (550, b'refused')}
            with self.assertRaisesRegex(RuntimeError, '^email delivery incomplete$'):
                self.sender().send(recipient='one@example.com,two@example.com', subject='提醒', text='正文')


class MemorySender:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.messages: list[dict[str, str]] = []

    def send(self, *, recipient: str, subject: str, text: str) -> None:
        if self.should_fail:
            raise RuntimeError("simulated SMTP failure")
        self.messages.append(
            {"recipient": recipient, "subject": subject, "text": text}
        )


def campaign(*, action_at: datetime = ACTION_AT, cancelled: bool = False) -> Campaign:
    return Campaign(
        id="campaign-a",
        ip_slug="genshin-impact",
        ip_name="原神",
        title="原神 × 示例品牌",
        status=EventStatus.CANCELLED if cancelled else EventStatus.UPCOMING,
        first_seen_at=NOW,
        updated_at=NOW,
        activities=[
            Activity(
                id="popup",
                kind=ActivityKind.POPUP,
                title="主题快闪",
                actions=[
                    EventAction(
                        id="sale-open",
                        kind=ActionKind.SALE_OPEN,
                        title="联动商品开售",
                        at=action_at,
                    )
                ],
            )
        ],
    )


def scheduled_job(*, expected_at: datetime = ACTION_AT) -> QueueJob:
    return QueueJob(
        id="job-scheduled",
        campaign_id="campaign-a",
        activity_id="popup",
        action_id="sale-open",
        kind=NotificationKind.DAY_BEFORE,
        due_date=DUE_DAY,
        expected_at=expected_at,
        semantic_key="campaign-a:sale-open:day-before",
        summary="明天开售",
    )


class NotificationServiceTests(unittest.TestCase):
    def test_internal_update_path_is_rendered_as_user_text(self):
        from herald.notifications import NotificationItem
        c = campaign()
        c.activities[0].actions[0].rules = '满300元赠一套'
        job = QueueJob(id='update', campaign_id=c.id, activity_id='popup', kind=NotificationKind.UPDATE,
            due_date=DUE_DAY, semantic_key='update', summary='更新：activities.popup.actions.sale-open.rules')
        text = NotificationService.build_cards([NotificationItem(job, c)])[0]['reasons'][0]['summary']
        self.assertIn('规则', text)
        self.assertIn('满300元赠一套', text)
        self.assertNotIn('activities.', text)

    def test_date_only_reason_does_not_display_invented_midnight(self):
        from herald.notifications import NotificationItem
        from herald.scheduler import ScheduleCompiler
        c = campaign()
        c.activities[0].actions[0].at = None
        c.activities[0].actions[0].start_date = date(2026, 9, 8)
        job = ScheduleCompiler().future_jobs(c, NOW)[0]
        card = NotificationService.build_cards([NotificationItem(job, c)])[0]
        self.assertEqual(card['reasons'][0]['date_only'], '2026-09-08')
        digest = NotificationService().render_digest([NotificationItem(job, c)], NOW)
        self.assertIn('2026-09-08', digest.text)
        self.assertNotIn('00:00', digest.text)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = StateStore(Path(self.temporary.name))
        self.store.initialize()
        self.service = NotificationService("Asia/Shanghai")

    def test_empty_daily_digest_is_opt_in_and_sent_only_once_per_day(self) -> None:
        sender = MemorySender()

        disabled = self.service.deliver_due(
            store=self.store,
            day=DUE_DAY,
            generated_at=NOW,
            sender=sender,
            recipient="player@example.com",
        )
        first = self.service.deliver_due(
            store=self.store,
            day=DUE_DAY,
            generated_at=NOW,
            sender=sender,
            recipient="player@example.com",
            send_empty_digest=True,
        )
        second = self.service.deliver_due(
            store=self.store,
            day=DUE_DAY,
            generated_at=NOW,
            sender=sender,
            recipient="player@example.com",
            send_empty_digest=True,
        )

        self.assertFalse(disabled.email_sent)
        self.assertTrue(first.email_sent)
        self.assertFalse(second.email_sent)
        self.assertEqual(first.sent, ())
        self.assertEqual(len(sender.messages), 1)
        self.assertIn("今日暂无需要关注的更新", sender.messages[0]["text"])
        self.assertTrue(
            self.store.has_receipt("daily-digest-2026-09-07", DUE_DAY)
        )

    def test_real_notification_still_sends_after_an_empty_daily_digest(self) -> None:
        sender = MemorySender()
        self.service.deliver_due(
            store=self.store,
            day=DUE_DAY,
            generated_at=NOW,
            sender=sender,
            recipient="player@example.com",
            send_empty_digest=True,
        )
        self.store.save_campaign(campaign())
        self.store.save_queue_job(scheduled_job())

        result = self.service.deliver_due(
            store=self.store,
            day=DUE_DAY,
            generated_at=NOW,
            sender=sender,
            recipient="player@example.com",
            send_empty_digest=True,
        )

        self.assertTrue(result.email_sent)
        self.assertEqual(len(result.sent), 1)
        self.assertEqual(len(sender.messages), 2)
        self.assertIn("明天开售", sender.messages[1]["text"])

    def test_saved_future_job_sends_without_any_new_source_content(self) -> None:
        self.store.save_campaign(campaign())
        self.store.save_queue_job(scheduled_job())
        sender = MemorySender()

        result = self.service.deliver_due(
            store=self.store,
            day=DUE_DAY,
            generated_at=NOW,
            sender=sender,
            recipient="player@example.com",
        )

        self.assertEqual(len(result.sent), 1)
        self.assertEqual(len(sender.messages), 1)
        self.assertIn("明天开售", sender.messages[0]["text"])
        self.assertTrue(self.store.has_receipt("job-scheduled", DUE_DAY))

    def test_update_and_due_action_share_one_card_and_keep_both_receipts(self) -> None:
        self.store.save_campaign(campaign())
        self.store.save_queue_job(scheduled_job())
        update = scheduled_job().model_copy(update={
            "id": "job-update", "action_id": None, "expected_at": None,
            "kind": NotificationKind.UPDATE, "summary": "补充预约说明",
            "semantic_key": "update",
        })
        self.store.save_queue_job(update)
        items, _ = self.service.collect_due(self.store, DUE_DAY)
        cards = self.service.build_cards(items)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["activity"]["title"], "主题快闪")
        self.assertEqual(len(cards[0]["reasons"]), 2)
        sender = MemorySender()
        self.service.deliver_due(store=self.store, day=DUE_DAY,
            generated_at=NOW, sender=sender, recipient="player@example.com")
        self.assertIn("1 项", sender.messages[0]["subject"])
        self.assertTrue(self.store.has_receipt(update.id, DUE_DAY))
        self.assertTrue(self.store.has_receipt("job-scheduled", DUE_DAY))
        public_items, _ = self.service.collect_due(self.store, DUE_DAY, include_delivered=True)
        self.assertEqual(self.service.build_cards(public_items), cards)

    def test_receipt_prevents_duplicate_delivery(self) -> None:
        self.store.save_campaign(campaign())
        self.store.save_queue_job(scheduled_job())
        sender = MemorySender()
        arguments = dict(
            store=self.store,
            day=DUE_DAY,
            generated_at=NOW,
            sender=sender,
            recipient="player@example.com",
            send_empty_digest=True,
        )

        self.service.deliver_due(**arguments)
        second = self.service.deliver_due(**arguments)

        self.assertEqual(len(sender.messages), 1)
        self.assertEqual(second.sent, ())

    def test_receipt_uses_local_delivery_day_when_utc_date_is_previous_day(self) -> None:
        local_day = date(2026, 9, 7)
        early_utc = datetime(2026, 9, 6, 16, 30, tzinfo=UTC)
        self.store.save_campaign(campaign())
        self.store.save_queue_job(scheduled_job())
        sender = MemorySender()

        arguments = dict(
            store=self.store,
            day=local_day,
            generated_at=early_utc,
            sender=sender,
            recipient="player@example.com",
        )
        self.service.deliver_due(**arguments)
        second = self.service.deliver_due(**arguments)

        self.assertTrue(self.store.has_receipt("job-scheduled", local_day))
        self.assertFalse(self.store.has_receipt("job-scheduled", early_utc.date()))
        self.assertEqual(second.sent, ())

    def test_stale_scheduled_job_is_skipped(self) -> None:
        self.store.save_campaign(campaign(action_at=ACTION_AT.replace(day=10)))
        self.store.save_queue_job(scheduled_job())
        sender = MemorySender()

        result = self.service.deliver_due(
            store=self.store,
            day=DUE_DAY,
            generated_at=NOW,
            sender=sender,
            recipient="player@example.com",
        )

        self.assertEqual(result.sent, ())
        self.assertEqual(result.skipped[0].id, "job-scheduled")
        self.assertEqual(sender.messages, [])

    def test_failed_send_does_not_write_receipt(self) -> None:
        self.store.save_campaign(campaign())
        self.store.save_queue_job(scheduled_job())
        sender = MemorySender(should_fail=True)

        with self.assertRaisesRegex(RuntimeError, "SMTP"):
            self.service.deliver_due(
                store=self.store,
                day=DUE_DAY,
                generated_at=NOW,
                sender=sender,
                recipient="player@example.com",
            )

        self.assertFalse(self.store.has_receipt("job-scheduled", DUE_DAY))

    def test_immediate_cancellation_notice_is_valid_even_after_campaign_cancelled(self) -> None:
        self.store.save_campaign(campaign(cancelled=True))
        self.store.save_queue_job(
            QueueJob(
                id="job-cancelled",
                campaign_id="campaign-a",
                kind=NotificationKind.UPDATE,
                due_date=DUE_DAY,
                semantic_key="campaign-a:cancelled",
                summary="活动已取消",
            )
        )
        sender = MemorySender()

        result = self.service.deliver_due(
            store=self.store,
            day=DUE_DAY,
            generated_at=NOW,
            sender=sender,
            recipient="player@example.com",
        )

        self.assertEqual(len(result.sent), 1)
        self.assertIn("活动已取消", sender.messages[0]["text"])


if __name__ == "__main__":
    unittest.main()
