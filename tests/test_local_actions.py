import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from tj_agent.local_actions import _scheduled_now, run_command, update_git_repo


def git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    )


class LocalActionScheduleTests(unittest.TestCase):
    def test_runs_after_schedule_only_once_per_day(self):
        config = {"scheduled_time": "0330"}
        self.assertFalse(_scheduled_now(config, {}, datetime(2026, 7, 11, 3, 29)))
        self.assertTrue(_scheduled_now(config, {}, datetime(2026, 7, 11, 3, 30)))
        state = {"last_attempt_date": "2026-07-11"}
        self.assertFalse(_scheduled_now(config, state, datetime(2026, 7, 11, 9, 0)))

    def test_command_failure_is_reported(self):
        result = run_command({"command": ["/usr/bin/false"]})
        self.assertEqual("error", result.status)
        self.assertIn("exit 1", result.message)


class GitUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.origin = root / "origin.git"
        self.source = root / "source"
        self.checkout = root / "checkout"

        subprocess.run(["git", "init", "--bare", str(self.origin)], check=True,
                       capture_output=True)
        subprocess.run(["git", "init", str(self.source)], check=True,
                       capture_output=True)
        git(self.source, "config", "user.email", "test@example.com")
        git(self.source, "config", "user.name", "Test User")
        git(self.source, "checkout", "-b", "master")
        (self.source / "tracked.txt").write_text("one\n")
        git(self.source, "add", "tracked.txt")
        git(self.source, "commit", "-m", "initial")
        git(self.source, "remote", "add", "origin", str(self.origin))
        git(self.source, "push", "-u", "origin", "master")
        subprocess.run(["git", "clone", str(self.origin), str(self.checkout)],
                       check=True, capture_output=True)

    def tearDown(self):
        self.tempdir.cleanup()

    def push_change(self):
        (self.source / "tracked.txt").write_text("two\n")
        git(self.source, "add", "tracked.txt")
        git(self.source, "commit", "-m", "remote change")
        git(self.source, "push")

    def test_fast_forwards_clean_checkout(self):
        self.push_change()
        result = update_git_repo(str(self.checkout), fetch_url=str(self.origin))
        self.assertEqual("updated", result.status)
        self.assertEqual("two\n", (self.checkout / "tracked.txt").read_text())

    def test_leaves_dirty_checkout_untouched(self):
        (self.checkout / "tracked.txt").write_text("local\n")
        original_head = git(self.checkout, "rev-parse", "HEAD").stdout.strip()
        self.push_change()

        result = update_git_repo(str(self.checkout))

        self.assertEqual("blocked", result.status)
        self.assertEqual(original_head, git(self.checkout, "rev-parse", "HEAD").stdout.strip())
        self.assertEqual("local\n", (self.checkout / "tracked.txt").read_text())


if __name__ == "__main__":
    unittest.main()
