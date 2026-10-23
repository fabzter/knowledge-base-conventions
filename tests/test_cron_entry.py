"""Exercise real script-only entrypoint execution against isolated fixture scripts."""
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cron_entry', ROOT / 'cron_entry.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


class CronEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.profile = Path(self.temp.name)
        self.script = self.profile / 'skills/personal-vault-para/scripts/sync_vault.py'
        self.script.parent.mkdir(parents=True)

    def test_runs_installed_script_without_an_agent(self):
        self.script.write_text('raise SystemExit(0)\n')
        self.assertEqual(entry.main([], profile_root=self.profile), 0)

    def test_nonzero_script_result_is_not_hidden(self):
        self.script.write_text('raise SystemExit(7)\n')
        self.assertEqual(entry.main([], profile_root=self.profile), 7)

    def test_missing_installed_script_is_explicit_failure(self):
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream):
            self.assertEqual(entry.main([], profile_root=self.profile), 1)
        self.assertIn('not installed', stream.getvalue())

    def test_timeout_has_nonzero_result_and_terminates_process(self):
        self.script.write_text('import time\ntime.sleep(30)\n')
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream):
            self.assertEqual(entry.main([], profile_root=self.profile, timeout=0.05), 1)
        self.assertIn('execution budget', stream.getvalue())


if __name__ == '__main__':
    unittest.main()
