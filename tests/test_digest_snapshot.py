import unittest
from tests import test_campaign_notices as fixtures
from herald.notifications import NotificationService
from herald.candidate_notices import resolve_candidates
from tests.test_pipeline import NOW


class DigestSnapshotTests(unittest.TestCase):
    setUp = fixtures.CampaignNoticeTests.setUp
    register = fixtures.CampaignNoticeTests.register
    def test_news_and_deadline_share_one_card_and_html_block(self):
        from herald.models import QueueJob
        from herald.notifications import NotificationItem
        self.register(self.a)
        resolve_candidates(self.store, NOW)
        service = NotificationService()
        due, _ = service.collect_due(self.store, NOW.date(), now=NOW)
        action = self.c.activities[0].actions[0]
        reminder = QueueJob(id='reminder', campaign_id=self.c.id, activity_id=self.c.activities[0].id,
            action_id=action.id, kind='day_before', due_date=NOW.date(), semantic_key='reminder', summary='明天开售', expected_at=action.at)
        due.append(NotificationItem(job=reminder, campaign=self.c))
        cards = service.build_cards(due)
        self.assertEqual(len(cards), 1)
        digest = service.render_digest(due, NOW)
        self.assertEqual(digest.html.count('<h3'), 1)
        self.assertIn('明天开售', digest.html)
    def test_failed_send_freezes_content_and_fact_receipts(self):
        self.register(self.a)
        resolve_candidates(self.store, NOW)
        sent = []
        class Sender:
            def send(self, **kwargs):
                sent.append(kwargs)
                if len(sent) == 1:
                    raise RuntimeError('offline failure')
        sender, service = Sender(), NotificationService()
        kwargs = dict(store=self.store, day=NOW.date(), generated_at=NOW, sender=sender, recipient='private@example.com')
        with self.assertRaises(RuntimeError):
            service.deliver_due(**kwargs)
        self.c.activities[0].actions[0].url = 'https://example.com/new'
        self.store.save_campaign(self.c)
        resolve_candidates(self.store, NOW)
        service.deliver_due(**kwargs)
        self.assertEqual(sent[0], sent[1])
        receipts = self.store.list_receipts()
        self.assertFalse(any('https://example.com/new' in r.facts.values() for r in receipts))
        snapshot = next((self.store.root / 'digest-snapshots').glob('*.json')).read_text(encoding='utf8')
        self.assertNotIn('private@example.com', snapshot)
