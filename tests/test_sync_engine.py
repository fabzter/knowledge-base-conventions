"""Offline safety regression tests. No real vault or network access."""
import copy
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sync_engine', ROOT / 'sync_engine.py')
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


class FakeSource:
    def __init__(self, texts=None, commit='a' * 40):
        self.texts = texts or {'00_meta/Rules.md': '# Rules\n', '00_meta/Research.md': '# Research\n'}
        self.commit = commit
        self.fail = False

    def pull(self):
        if self.fail:
            raise engine.SyncError('network unavailable')
        manifest = {'schema_version': 1, 'files': [
            {'target': target, 'source': 'vault/' + target, 'sha256': sha(text)}
            for target, text in sorted(self.texts.items())]}
        return self.commit, manifest, {'vault/' + p: text.encode() for p, text in self.texts.items()}


class FakeVault:
    def __init__(self, notes=None):
        self.notes = dict(notes or {})
        self.writes = []
        self.fail_at = None
        self.fail_after_write = False
        self.mismatch = False
        self.read_count = {}
        self.mutate_on_read = None

    def verify(self):
        if self.mismatch:
            raise engine.SyncError('vault path mismatch')

    def read(self, path):
        self.read_count[path] = self.read_count.get(path, 0) + 1
        if self.mutate_on_read and self.mutate_on_read(path, self.read_count[path]):
            self.notes[path] = 'concurrent local edit\n'
        return self.notes.get(path)

    def write(self, path, text):
        if self.fail_at == path and not self.fail_after_write:
            raise engine.SyncError('native write unavailable')
        self.notes[path] = text
        self.writes.append(path)
        if self.fail_at == path:
            raise engine.SyncError('native response lost after persistence')

    def verify_disk(self, path, text):
        return 'verified'


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / 'state'
        self.source = FakeSource()
        self.vault = FakeVault()
        self.config = {
            'repo': 'example/conventions', 'ref': 'main', 'vault': 'personal',
            'expected_vault_path': str(Path(self.temp.name) / 'vault'),
            'obsidian_cli': '/usr/bin/false', 'state_dir': str(self.state),
            'allowed_paths': list(self.source.texts),
        }

    def run_sync(self, dry_run=False):
        return engine.run_sync(self.config, source=self.source, backend=self.vault,
                               dry_run=dry_run)

    def state_json(self):
        return json.loads((self.state / 'state.json').read_text())

    def test_initial_sync_writes_all_and_status(self):
        result = self.run_sync()
        self.assertEqual(set(result['changed']), set(self.source.texts) | {engine.STATUS_PATH})
        self.assertEqual(self.state_json()['last_complete_commit'], self.source.commit)
        self.assertEqual(self.vault.notes['00_meta/Rules.md'], '# Rules\n')

    def test_repeat_is_noop_and_status_does_not_churn(self):
        self.run_sync()
        before = self.vault.notes.copy()
        self.vault.writes.clear()
        self.assertEqual(self.run_sync()['changed'], [])
        self.assertEqual(self.vault.writes, [])
        self.assertEqual(self.vault.notes, before)

    def test_existing_identical_notes_can_be_adopted(self):
        self.vault.notes.update(self.source.texts)
        result = self.run_sync()
        self.assertEqual(result['changed'], [engine.STATUS_PATH])

    def test_unknown_different_note_blocks_entire_batch(self):
        self.vault.notes['00_meta/Rules.md'] = 'private existing note\n'
        with self.assertRaises(engine.SyncError):
            self.run_sync()
        self.assertEqual(self.vault.writes, [])
        self.assertEqual(self.vault.notes['00_meta/Rules.md'], 'private existing note\n')

    def test_local_edit_blocks_without_overwrite(self):
        self.run_sync()
        self.vault.writes.clear()
        self.vault.notes['00_meta/Rules.md'] += 'local change\n'
        with self.assertRaises(engine.SyncError):
            self.run_sync()
        self.assertEqual(self.vault.writes, [])
        self.assertIn('local change', self.vault.notes['00_meta/Rules.md'])

    def test_local_deletion_is_not_silently_undone(self):
        self.run_sync()
        del self.vault.notes['00_meta/Rules.md']
        self.vault.writes.clear()
        with self.assertRaises(engine.SyncError):
            self.run_sync()
        self.assertNotIn('00_meta/Rules.md', self.vault.notes)
        self.assertEqual(self.vault.writes, [])

    def test_unmanaged_private_note_is_untouched(self):
        self.vault.notes['00_meta/Local Vault Conventions.md'] = 'private\n'
        self.run_sync()
        self.assertEqual(self.vault.notes['00_meta/Local Vault Conventions.md'], 'private\n')
        self.assertNotIn('00_meta/Local Vault Conventions.md', self.vault.writes)

    def test_source_failure_leaves_last_good_content(self):
        self.run_sync()
        previous = self.vault.notes.copy()
        self.source.fail = True
        with self.assertRaises(engine.SyncError):
            self.run_sync()
        self.assertEqual(self.vault.notes, previous)
        self.assertEqual(json.loads((self.state / 'health.json').read_text())['ok'], False)

    def test_vault_mismatch_prevents_all_writes(self):
        self.vault.mismatch = True
        with self.assertRaises(engine.SyncError):
            self.run_sync()
        self.assertEqual(self.vault.writes, [])

    def test_dry_run_does_not_write_notes_or_state(self):
        result = self.run_sync(dry_run=True)
        self.assertTrue(result['changed'])
        self.assertEqual(self.vault.writes, [])
        self.assertFalse((self.state / 'state.json').exists())
        self.assertFalse((self.state / 'health.json').exists())

    def test_upstream_change_backs_up_old_document(self):
        self.run_sync()
        self.source.texts['00_meta/Rules.md'] = '# New rules\n'
        self.source.commit = 'b' * 40
        self.run_sync()
        backups = list((self.state / 'backups').rglob('*.md'))
        self.assertTrue(any(p.read_text() == '# Rules\n' for p in backups))
        self.assertEqual(self.vault.notes['00_meta/Rules.md'], '# New rules\n')

    def test_partial_failure_retains_checkpoint_and_resumes(self):
        self.vault.fail_at = '00_meta/Rules.md'
        with self.assertRaises(engine.SyncError):
            self.run_sync()
        self.assertNotIn('last_complete_commit', self.state_json())
        self.assertIn('00_meta/Research.md', self.state_json()['files'])
        self.vault.fail_at = None
        self.run_sync()
        self.assertEqual(self.state_json()['last_complete_commit'], self.source.commit)

    def test_lost_response_after_write_recovered_from_journal(self):
        self.vault.fail_at = '00_meta/Rules.md'
        self.vault.fail_after_write = True
        with self.assertRaises(engine.SyncError):
            self.run_sync()
        self.assertIn('00_meta/Rules.md', self.state_json()['inflight'])
        self.vault.fail_at = None
        self.vault.writes.clear()
        self.run_sync()
        self.assertNotIn('00_meta/Rules.md', self.vault.writes)
        self.assertEqual(self.state_json()['last_complete_commit'], self.source.commit)

    def test_changed_content_after_interrupted_write_is_not_adopted(self):
        self.vault.fail_at = '00_meta/Rules.md'
        self.vault.fail_after_write = True
        with self.assertRaises(engine.SyncError):
            self.run_sync()
        self.vault.fail_at = None
        self.vault.notes['00_meta/Rules.md'] = 'unrelated local edit\n'
        self.vault.writes.clear()
        with self.assertRaises(engine.SyncError):
            self.run_sync()
        self.assertEqual(self.vault.writes, [])

    def test_recheck_catches_concurrent_modification(self):
        self.run_sync()
        self.vault.read_count.clear()
        self.source.texts['00_meta/Rules.md'] = 'new upstream\n'
        self.source.commit = 'b' * 40
        self.vault.mutate_on_read = lambda path, count: path == '00_meta/Rules.md' and count == 2
        self.vault.writes.clear()
        with self.assertRaises(engine.SyncError):
            self.run_sync()
        self.assertEqual(self.vault.notes['00_meta/Rules.md'], 'concurrent local edit\n')
        self.assertNotIn('00_meta/Rules.md', self.vault.writes)

    def test_status_local_edit_also_blocks_batch(self):
        self.run_sync()
        self.vault.notes[engine.STATUS_PATH] = 'edited status\n'
        self.vault.writes.clear()
        self.source.commit = 'b' * 40
        with self.assertRaises(engine.SyncError):
            self.run_sync()
        self.assertEqual(self.vault.writes, [])

    def test_source_removal_never_deletes_local_note(self):
        self.run_sync()
        del self.source.texts['00_meta/Rules.md']
        self.vault.writes.clear()
        with self.assertRaises(engine.SyncError):
            self.run_sync()
        self.assertEqual(self.vault.writes, [])
        self.assertIn('00_meta/Rules.md', self.vault.notes)

    def test_corrupt_state_is_not_overwritten(self):
        self.run_sync()
        (self.state / 'state.json').write_text('broken json')
        self.vault.writes.clear()
        with self.assertRaises(engine.SyncError):
            self.run_sync()
        self.assertEqual((self.state / 'state.json').read_text(), 'broken json')
        self.assertEqual(self.vault.writes, [])

    def test_exclusive_lock_rejects_overlap(self):
        self.state.mkdir(parents=True)
        with engine.exclusive_lock(self.state):
            with self.assertRaises(engine.SyncError):
                self.run_sync()
        self.assertEqual(self.vault.writes, [])


class ValidationTests(unittest.TestCase):
    def payload(self):
        source = FakeSource({'00_meta/Rules.md': 'hello\n'})
        return source.pull()

    def test_valid_payload(self):
        commit, manifest, files = self.payload()
        result = engine.validate_payload(commit, manifest, files, ['00_meta/Rules.md'])
        self.assertEqual(result, {'00_meta/Rules.md': 'hello\n'})

    def test_hash_corruption(self):
        commit, manifest, files = self.payload()
        files['vault/00_meta/Rules.md'] = b'corrupt\n'
        with self.assertRaises(engine.SyncError):
            engine.validate_payload(commit, manifest, files, ['00_meta/Rules.md'])

    def test_unsafe_or_expanded_target(self):
        for target in ['../private.md', '/tmp/private.md', '01_projects/private.md',
                       '00_meta/../private.md', '00_meta/evil.py', '00_meta/a\\b.md',
                       '00_meta/unknown.md', '00_meta/x\ny.md']:
            commit, manifest, files = self.payload()
            manifest['files'][0]['target'] = target
            manifest['files'][0]['source'] = 'vault/' + target
            with self.subTest(target=target), self.assertRaises(engine.SyncError):
                engine.validate_payload(commit, manifest, files, ['00_meta/Rules.md'])

    def test_missing_expected_target_rejected(self):
        commit, manifest, files = self.payload()
        with self.assertRaises(engine.SyncError):
            engine.validate_payload(commit, manifest, files,
                                    ['00_meta/Rules.md', '00_meta/Missing.md'])

    def test_nonimmutable_commit_and_duplicate_entry_rejected(self):
        commit, manifest, files = self.payload()
        with self.assertRaises(engine.SyncError):
            engine.validate_payload('main', manifest, files, ['00_meta/Rules.md'])
        manifest['files'].append(copy.deepcopy(manifest['files'][0]))
        with self.assertRaises(engine.SyncError):
            engine.validate_payload(commit, manifest, files, ['00_meta/Rules.md'])

    def test_source_mapping_and_schema_mismatch_rejected(self):
        commit, manifest, files = self.payload()
        manifest['files'][0]['source'] = 'other/Rules.md'
        with self.assertRaises(engine.SyncError):
            engine.validate_payload(commit, manifest, files, ['00_meta/Rules.md'])
        commit, manifest, files = self.payload()
        manifest['schema_version'] = 2
        with self.assertRaises(engine.SyncError):
            engine.validate_payload(commit, manifest, files, ['00_meta/Rules.md'])

    def test_oversize_and_non_utf8_rejected(self):
        for data in [b'x' * (128 * 1024 + 1), b'\xff', b'nul\x00byte']:
            commit, manifest, files = self.payload()
            files['vault/00_meta/Rules.md'] = data
            manifest['files'][0]['sha256'] = hashlib.sha256(data).hexdigest()
            with self.subTest(data_length=len(data)), self.assertRaises(engine.SyncError):
                engine.validate_payload(commit, manifest, files, ['00_meta/Rules.md'])

    def test_github_source_uses_one_commit_for_every_download(self):
        config = {'repo': 'example/conventions', 'ref': 'main'}
        source = engine.GitHubSource(config, engine.Deadline())
        commit, manifest, files = self.payload()
        urls = []
        def fake_get(url):
            urls.append(url)
            if '/commits/' in url:
                return json.dumps({'sha': commit}).encode()
            if url.endswith('/manifest.json'):
                return json.dumps(manifest).encode()
            return files['vault/00_meta/Rules.md']
        with patch.object(source, '_get', side_effect=fake_get):
            actual, _, _ = source.pull()
        self.assertEqual(actual, commit)
        self.assertTrue(all('/' + commit + '/' in url for url in urls[1:]))


class NativeContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = {
            'repo': 'example/conventions', 'ref': 'main', 'vault': 'personal',
            'expected_vault_path': str(self.root / 'vault'),
            'obsidian_cli': '/usr/bin/false', 'state_dir': str(self.root / 'state'),
            'allowed_paths': ['00_meta/Rules.md'], 'command_timeout': 15,
        }
        self.backend = engine.ObsidianBackend(self.config, engine.Deadline())

    def test_native_read_preserves_unicode_and_trailing_newlines(self):
        text = 'Caf\u00e9 \u2615\n\n'
        metadata = f'path\t00_meta/Rules.md\nsize\t{len(text.encode())}\n'
        with patch.object(self.backend, '_call', side_effect=['00_meta/Rules.md\n', text, metadata]):
            self.assertEqual(self.backend.read('00_meta/Rules.md'), text)

    def test_partial_native_read_is_rejected_by_byte_size(self):
        with patch.object(self.backend, '_call', side_effect=[
                '00_meta/Rules.md\n', 'truncated', 'path\t00_meta/Rules.md\nsize\t100\n']):
            with self.assertRaises(engine.SyncError):
                self.backend.read('00_meta/Rules.md')

    def test_native_error_with_zero_exit_is_failure(self):
        process = subprocess.CompletedProcess([], 0, 'Error: app unavailable\n', '')
        with patch.object(engine.subprocess, 'run', return_value=process):
            with self.assertRaises(engine.SyncError):
                self.backend._call('read', 'path=00_meta/Rules.md')

    def test_new_file_does_not_request_blind_overwrite(self):
        with patch.object(self.backend, '_call') as call:
            self.backend.write('00_meta/Rules.md', 'text\n')
            self.assertNotIn('overwrite', call.call_args.args)
            self.backend.seen['00_meta/Rules.md'] = True
            self.backend.write('00_meta/Rules.md', 'text\n')
            self.assertIn('overwrite', call.call_args.args)

    def test_symlinked_note_or_parent_is_blocked(self):
        vault = self.root / 'vault'
        vault.mkdir()
        target = self.root / 'elsewhere'
        target.mkdir()
        (vault / '00_meta').symlink_to(target, target_is_directory=True)
        with self.assertRaises(engine.SyncError):
            self.backend._guard('00_meta/Rules.md')

    def test_private_state_cannot_be_placed_in_vault(self):
        self.config['state_dir'] = str(self.root / 'vault/private-state')
        with self.assertRaises(engine.SyncError):
            engine.validate_config(self.config)

    def test_disk_permission_limit_is_not_a_native_write_failure(self):
        process = subprocess.CompletedProcess([], 0, '{"status":"unavailable"}\n', '')
        with patch.object(engine.subprocess, 'run', return_value=process):
            self.assertIn('unavailable', self.backend.verify_disk('00_meta/Rules.md', 'text\n'))

    def test_expired_budget_fails_closed(self):
        with self.assertRaises(engine.SyncError):
            engine.Deadline(seconds=-1).timeout()

    def test_cli_noop_has_empty_stdout(self):
        config = self.root / 'config.json'
        config.write_text(json.dumps(self.config))
        stream = io.StringIO()
        with patch.object(engine, 'run_sync', return_value={'changed': []}), contextlib.redirect_stdout(stream):
            self.assertEqual(engine.main(['--config', str(config)]), 0)
        self.assertEqual(stream.getvalue(), '')

    def test_cli_failure_is_nonzero_and_in_stderr(self):
        config = self.root / 'config.json'
        config.write_text(json.dumps(self.config))
        stream = io.StringIO()
        with patch.object(engine, 'run_sync', side_effect=engine.SyncError('conflict')), contextlib.redirect_stderr(stream):
            self.assertEqual(engine.main(['--config', str(config)]), 1)
        self.assertIn('conflict', stream.getvalue())


if __name__ == '__main__':
    unittest.main()
