"""Offline checks for the experimental review, never calls real AI."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('two_pass', Path(__file__).resolve().parents[1] / 'scripts/two_pass_review.py')
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


class TwoPassReviewTests(unittest.TestCase):
    def test_routes_must_cover_every_post(self):
        with self.assertRaises(ValueError):
            review.validate_routes({'groups': [], 'excluded': [], 'pending': []}, {'s1': {}}, {})

    def test_rejects_unknown_existing_campaign(self):
        with self.assertRaises(ValueError):
            review.validate_routes({'groups': [{'id': 'missing', 'phrase': '联动', 'sources': ['s1']}], 'excluded': [], 'pending': []}, {'s1': {}}, {})

    def test_same_fact_adds_evidence_without_new_notification(self):
        old = {'facts': {'预约/开放日期': {'value': '2026-10-26', 'evidence': []}}}
        result = {'facts': [{'key': '预约/开放日期', 'value': '2026-10-26', 'source_id': 's1', 'quote': '10月26日开放预约'}]}
        updated, delta = review.apply_facts(old, result, {'s1': {'text': '活动10月26日开放预约。'}})
        self.assertEqual(delta, [])
        self.assertEqual(len(updated['facts']['预约/开放日期']['evidence']), 1)

    def test_changed_fact_has_before_and_after(self):
        old = {'facts': {'预约/开放日期': {'value': '2026-10-26', 'evidence': []}}}
        updated, delta = review.apply_facts(old, {'facts': [{'key': '预约/开放日期', 'value': '2026-10-27', 'source_id': 's1', 'quote': '改至10月27日'}]}, {'s1': {'text': '改至10月27日'}})
        self.assertEqual(delta[0]['before'], '2026-10-26')
        self.assertEqual(delta[0]['after'], '2026-10-27')

    def test_invalid_evidence_does_not_mutate_baseline(self):
        old = {'facts': {}}
        with self.assertRaises(ValueError):
            review.apply_facts(old, {'facts': [{'key': '时间', 'value': '明日', 'source_id': 's1', 'quote': '不存在'}]}, {'s1': {'text': '待公布'}})
        self.assertEqual(old, {'facts': {}})

    def test_multiple_campaigns_can_share_one_post(self):
        review.validate_routes({'groups': [{'id': None, 'phrase': p, 'sources': ['s1']} for p in ['甲联动', '乙联动']], 'excluded': [], 'pending': []}, {'s1': {}}, {})
