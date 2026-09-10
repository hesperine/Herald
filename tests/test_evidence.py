import unittest

from herald.evidence import quote_matches


class EvidenceTests(unittest.TestCase):
    def test_format_tolerance(self):
        self.assertTrue(quote_matches('活动时间: 10月3日', '公告：活动时间：１０月３日\n开放'))
        self.assertTrue(quote_matches('主题店「秋风」开放', '主题店“秋风”开放'))

    def test_local_punctuation_alignment(self):
        self.assertTrue(quote_matches('主题店将于10月3日开放', '公告：主题店，将于10月3日开放。欢迎参与'))

    def test_substantive_edits_rejected(self):
        for quote in ('活动将于10月3日正式开放', '活动将于10月4日开放', '活动将于10月3日关闭'):
            self.assertFalse(quote_matches(quote, '活动将于10月3日开放'))
        self.assertFalse(quote_matches('可以预约', '不可以预约'))
        self.assertFalse(quote_matches('', '公告'))
        self.assertFalse(quote_matches('！！！', '公告！'))
