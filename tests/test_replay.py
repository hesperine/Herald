import unittest
from herald.replay import split_materials
from test_dedupe import make_observation
from datetime import timedelta


class ReplayTests(unittest.TestCase):
    def test_split_uses_publication_time_and_excludes_fewshot(self):
        a = make_observation('a', text='真实候选', account='official')
        b = make_observation('b', text='后续内容', account='official')
        b.source.published_at += timedelta(days=2)
        train, incremental = split_materials([b, a], a.source.published_at + timedelta(days=1))
        self.assertEqual([p.id for p in train], ['a'])
        self.assertEqual([p.id for p in incremental], ['b'])
        a.text = '大白兔联动'
        with self.assertRaises(ValueError):
            split_materials([a, b], a.source.published_at + timedelta(days=1))

    def test_empty_phase_allowed_but_empty_dataset_rejected(self):
        a = make_observation('a', text='公告', account='official')
        before, after = split_materials([a], a.source.published_at)
        self.assertEqual(before, [])
        self.assertEqual(after, [a])
        with self.assertRaises(ValueError):
            split_materials([], a.source.published_at)
