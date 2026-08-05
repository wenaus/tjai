import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import research_multimodel  # noqa: E402
from tjai_app import action_runner  # noqa: E402


class ResearchCodexTests(unittest.TestCase):
    def test_command_loads_user_config_and_requires_tjai_mcp(self):
        command, token_env_name = (
            research_multimodel._build_subscription_codex_command(
                '/usr/bin/codex', Path('/tmp/research-output.md'))
        )

        self.assertNotIn('--ignore-user-config', command)
        self.assertEqual(command[1:4], ['--ask-for-approval', 'never', 'exec'])
        self.assertIn('read-only', command)
        self.assertIn('web_search="live"', command)
        self.assertIn('mcp_servers.tjai.required=true', command)
        self.assertEqual(token_env_name, 'TJAI_CODEX_MCP_TOKEN')

    def test_workdir_uses_configured_git_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp)
            (checkout / '.git').mkdir()
            with mock.patch.dict(
                    os.environ, {'TJAI_RESEARCH_WORKDIR': str(checkout)}):
                self.assertEqual(
                    research_multimodel._codex_research_workdir(), checkout)

    def test_codex_prompt_does_not_duplicate_inline_reader_context(self):
        with (
            mock.patch.object(
                action_runner,
                '_system_prompt_content_for_model',
                return_value='MODEL PROMPT',
            ),
            mock.patch.object(
                action_runner,
                'load_reader_context',
                return_value='INLINE READER CONTEXT',
            ) as load_context,
        ):
            prompt = action_runner.build_research_prompt(
                'Research topic', 'chatgpt')

        load_context.assert_not_called()
        self.assertNotIn('INLINE READER CONTEXT', prompt)
        self.assertIn('MODEL PROMPT', prompt)
        self.assertIn('Research topic', prompt)


if __name__ == '__main__':
    unittest.main()
