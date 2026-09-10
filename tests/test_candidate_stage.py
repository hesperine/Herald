import unittest
from herald.candidate_stage import prepare_candidates
from test_dedupe import make_observation


class CandidateStageTests(unittest.TestCase):
    def test_deduplication_precedes_filter_and_keeps_evidence(self):
        items = [make_observation('a', text='官方嘉年华开票', account='a'),
                 make_observation('b', text='官方嘉年华开票', account='b'),
                 make_observation('c', text='版本维护补偿', account='a')]
        groups = prepare_candidates(items, ['明日方舟'])
        passed = [g for g in groups if g.accepted]
        self.assertEqual(len(passed), 1)
        self.assertEqual(passed[0].group.observation_ids, ('a', 'b'))
        self.assertEqual(len(groups), 2)
