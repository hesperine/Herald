import importlib.util
import json
from datetime import datetime
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('preview_ui', ROOT / 'scripts/preview-ui.py')
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


class PreviewUITests(unittest.TestCase):
    def test_offline_preview_exercises_gallery_email_and_history_boundary(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'demo'
            preview.build_preview(output, datetime.fromisoformat('2026-10-15T12:00:00+08:00'))
            page = output / 'page'
            dates = json.loads((page / 'data/reminders/index.json').read_text('utf8'))['dates']
            self.assertEqual(dates, [f'2026-10-{day:02}' for day in range(15, 8, -1)])
            self.assertFalse((output / 'state/reminders/2026-10-08.json').exists())
            self.assertFalse((page / 'events/preview-ended.json').exists())
            historical = json.loads((page / 'data/reminders/2026-10-14.json').read_text('utf8'))
            archived = next(c for c in historical['cards'] if c['campaign_id'] == 'preview-ended')
            self.assertIsNone(archived['detail_url'])
            detail = json.loads((page / 'events/preview.json').read_text('utf8'))
            self.assertEqual(len(detail['media']), 3)
            for media in detail['media']:
                self.assertTrue((page / media['asset_path']).is_file())
            self.assertIn('即将开始或结束', (page / 'email-preview.html').read_text('utf8'))
            self.assertIn('今日新消息', (page / 'email-preview.html').read_text('utf8'))
            self.assertIn('暂无需要关注', (page / 'email-empty.html').read_text('utf8'))
            self.assertIn('演示数据', (page / 'preview.html').read_text('utf8'))
            with self.assertRaises(FileExistsError):
                preview.build_preview(output, datetime.fromisoformat('2026-10-15T12:00:00+08:00'))
