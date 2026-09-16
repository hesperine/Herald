import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('debug_web', Path(__file__).resolve().parents[1] / 'scripts/debug-web.py')
web = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web)


class DebugWebTests(unittest.TestCase):
    def test_paths_cannot_escape_workspace(self):
        with self.assertRaises(ValueError):
            web.safe_path('../local.env')
        with self.assertRaises(ValueError):
            web.safe_path('x/../../local.env')

    def test_parameters_reject_unbounded_or_invalid_input(self):
        with self.assertRaises(ValueError):
            web.validate_options({'max_tokens': 999999})
        with self.assertRaises(ValueError):
            web.validate_options({'batch_size': 0})
        with self.assertRaises(ValueError):
            web.validate_options({'thinking': 'oops'})

    def test_results_show_rejected_extraction_as_well_as_saved_facts(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trace = root / 'traces/t'; trace.mkdir(parents=True)
            (trace/'001.json').write_text(json.dumps({'model':'test','status':200,'messages':[],
                'answer':json.dumps({'groups':[{'observation_ids':['p'],'extraction':{'activities':[]}}]})}),encoding='utf-8')
            (trace/'after.json').write_text('[]',encoding='utf-8')
            result = web.read_results(root)
            self.assertEqual(result['calls'][0]['parsed']['groups'][0]['observation_ids'], ['p'])
            self.assertEqual(result['campaigns'], [])
