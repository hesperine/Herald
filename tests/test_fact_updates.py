import unittest
from datetime import timedelta

from herald.fact_updates import FactUpdate, apply_fact_updates
from herald.models import SourceObservation
from test_merge import BASE, campaign, source


class FactUpdateTests(unittest.TestCase):
    def material(self, identifier, day, text):
        return SourceObservation(id=identifier, source=source(identifier, BASE + timedelta(days=day)), text=text)

    def update(self, item, path, value):
        return FactUpdate(field_path=path, value=value, observation_id=item.id, quote=item.text)

    def test_old_post_cannot_reverse_date_but_can_supply_missing_partner(self):
        original = campaign('c')
        original.partner = None
        new = self.material('new', 3, '活动延期至新日期')
        path = 'activities.national-sale.start_at'
        current = apply_fact_updates(original, [self.update(new, path, (BASE + timedelta(days=12)).isoformat())], [new], BASE)
        old = self.material('old', 1, '旧日期，合作方甲')
        result = apply_fact_updates(current, [self.update(old, path, BASE.isoformat()), self.update(old, 'partner', '甲')], [old], BASE)
        self.assertEqual(result.activities[0].start_at, BASE + timedelta(days=12))
        self.assertEqual(result.partner, '甲')
        self.assertIsNone(original.activities[0].start_at)

    def test_replay_is_idempotent_and_omission_preserves_fields(self):
        item = self.material('p', 1, '新标题')
        original = campaign('c')
        updates = [self.update(item, 'title', '新标题')]
        result = apply_fact_updates(original, updates, [item], BASE)
        replay = apply_fact_updates(result, updates, [item], BASE + timedelta(days=2))
        self.assertEqual(result, replay)
        self.assertEqual(result.partner, original.partner)

    def test_invalid_target_quote_or_null_rejected_without_mutation(self):
        item = self.material('p', 1, '公告')
        original = campaign('c')
        for path, value, quote in [('id', 'other', '公告'), ('title', None, '公告'), ('title', 'x', '不存在'), ('activities.missing.title', 'x', '公告')]:
            with self.subTest(path=path, quote=quote), self.assertRaises(ValueError):
                apply_fact_updates(original, [FactUpdate(field_path=path, value=value, observation_id=item.id, quote=quote)], [item], BASE)
        self.assertEqual(original.revision, 1)

    def test_invalid_time_range_rejected(self):
        item = self.material('p', 1, '日期')
        with self.assertRaises(ValueError):
            apply_fact_updates(campaign('c'), [self.update(item, 'activities.national-sale.start_at', (BASE + timedelta(days=2)).isoformat()), self.update(item, 'activities.national-sale.end_at', BASE.isoformat())], [item], BASE)

    def test_equal_time_conflict_rejected(self):
        item = self.material('p', 1, '标题')
        current = apply_fact_updates(campaign('c'), [self.update(item, 'title', 'A')], [item], BASE)
        with self.assertRaises(ValueError):
            apply_fact_updates(current, [self.update(item, 'title', 'B')], [item], BASE)
