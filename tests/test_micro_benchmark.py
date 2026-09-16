import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('micro_benchmark',ROOT/'scripts/micro-benchmark.py')
bench=importlib.util.module_from_spec(spec);spec.loader.exec_module(bench)


class MicroBenchmarkTests(unittest.TestCase):
    def test_frozen_inputs_and_draft_expectations(self):
        suite=bench.load_suite()
        self.assertEqual(len(suite['cases']),5)
        self.assertEqual(suite['review_status'],'draft')
        for case in suite['cases']:
            data=bench.dataset(case)
            from herald.models import SourceObservation
            for observation in data['observations']:
                SourceObservation.model_validate(observation)
            self.assertTrue(data['observations'])
            self.assertTrue(case['checks'])
            for check in case['checks']:
                self.assertTrue(check['id'])
                self.assertTrue(check['expectation'])
                for ev in check.get('evidence',[]):
                    text=next(o['text'] for o in data['observations'] if o['id']==ev['source_id'])
                    self.assertIn(ev['quote'],text)

    def test_batch_and_incremental_use_identical_posts_different_split(self):
        cases={c['id']:c for c in bench.load_suite()['cases']}
        batch=bench.dataset(cases['hongshan-batch'])
        incremental=bench.dataset(cases['hongshan-incremental'])
        self.assertEqual(batch['observations'],incremental['observations'])
        self.assertNotEqual(batch['cutoff'],incremental['cutoff'])

    def test_prepare_is_offline_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'run'
            bench.prepare(output)
            self.assertEqual(len(list(output.glob('*/dataset.json'))),5)
            self.assertTrue((output/'benchmark-manifest.json').exists())
            with self.assertRaises(FileExistsError):bench.prepare(output)
