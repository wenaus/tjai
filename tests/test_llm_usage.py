from datetime import datetime, timedelta, timezone
import unittest

from tjai_app.llm_usage import parse_codex_total_tokens, summarize_codex_usage


class LlmUsageTests(unittest.TestCase):
    def test_parse_codex_total_tokens(self):
        output = 'work log\ntokens used\n978,134\nfinished'
        self.assertEqual(parse_codex_total_tokens(output), 978134)
        self.assertIsNone(parse_codex_total_tokens('timed out before footer'))

    def test_summarize_codex_usage_tracks_missing_and_timeouts(self):
        now = datetime(2026, 7, 11, 12, tzinfo=timezone.utc)
        events = [
            {
                'timestamp': now - timedelta(hours=1),
                'action_id': 'picks-agent',
                'model': 'gpt-5.6-sol',
                'effort': 'xhigh',
                'exit_code': 0,
                'usage_reported': True,
                'total_tokens': 900000,
            },
            {
                'timestamp': now - timedelta(hours=2),
                'action_id': 'daily-assessment',
                'model': 'gpt-5.6-sol',
                'effort': 'xhigh',
                'exit_code': 124,
                'usage_reported': False,
            },
            {
                'timestamp': now - timedelta(days=2),
                'action_id': 'picks-agent',
                'model': 'gpt-5.6-sol',
                'effort': 'xhigh',
                'exit_code': 0,
                'usage_reported': True,
                'total_tokens': 300000,
            },
        ]

        summary = summarize_codex_usage(events, now)
        self.assertEqual(summary['windows']['24h'], {
            'runs': 2,
            'reported_runs': 1,
            'total_tokens': 900000,
            'unreported_runs': 1,
            'timeouts': 1,
            'avg_tokens': 900000,
        })
        self.assertEqual(summary['windows']['7d']['total_tokens'], 1200000)
        self.assertEqual(summary['actions'][0]['action_id'], 'picks-agent')
        self.assertEqual(summary['actions'][0]['7d']['avg_tokens'], 600000)
