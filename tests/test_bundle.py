"""Offline regression checks for manifests, public docs and pinned skill bundles."""
import copy
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("kb_bundle", ROOT / "tools/bundle.py")
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


class ManifestTests(unittest.TestCase):
    def test_committed_manifest_matches_exact_documents(self):
        self.assertEqual((ROOT / "manifest.json").read_bytes(),
                         bundle.json_bytes(bundle.manifest(ROOT)))

    def test_every_target_maps_to_itself_under_vault(self):
        for item in bundle.manifest(ROOT)["files"]:
            self.assertEqual(item["source"], "vault/" + item["target"])
            self.assertTrue(item["target"].startswith("00_meta/"))
            self.assertEqual(len(item["sha256"]), 64)

    def test_public_documents_have_no_private_identifiers(self):
        for path in [*(ROOT / "vault/00_meta").glob("*.md"),
                     ROOT / "skill/SKILL.md", ROOT / "config.example.json"]:
            text = path.read_text()
            self.assertNotRegex(text, r"/Users/[^/\s]+|GoogleDrive-|[\w.+-]+@(?:gmail|outlook)\.com")
            self.assertNotRegex(text, r"@session:|\b(?:CURP|CLABE)\s*[:=]")

    def test_wikilinks_stay_in_governance_and_resolve_to_declared_or_local_notes(self):
        targets = {"00_meta/" + Path(p).stem for p in bundle.DOCS}
        targets.update({"00_meta/Local Vault Conventions", "00_meta/Knowledge Base Sync Status"})
        for path in (ROOT / "vault/00_meta").glob("*.md"):
            for target in re.findall(r"\[\[([^]|]+)", path.read_text()):
                self.assertIn(target, targets, (path, target))

    def test_all_skill_links_are_declared_support_files(self):
        declared = set(bundle.BUNDLE_SOURCES) | {bundle.LOCK}
        for target in re.findall(r"\]\(((?:references|scripts)/[^)\s]+)\)",
                                 (ROOT / "skill/SKILL.md").read_text()):
            self.assertIn(target, declared)

    def test_traversal_and_noncanonical_paths_rejected(self):
        for value in ["../file", "/tmp/file", "a/../file", "a\\file", "a//file", "a/./file", "a\nfile", ""]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                bundle.safe_relative(value)


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "skill"
        self.commit = "a" * 40
        self.payload = {dest: (ROOT / source).read_bytes()
                        if (ROOT / source).is_file() else b"fixture source\n"
                        for dest, source in bundle.BUNDLE_SOURCES.items()}
        self.lock = {
            "schema_version": 1, "repo": bundle.REPOSITORY, "commit": self.commit,
            "files": {dest: {"source": bundle.BUNDLE_SOURCES[dest], "sha256": bundle.digest(data)}
                      for dest, data in self.payload.items()},
        }
        for dest, data in {**self.payload, bundle.LOCK: bundle.json_bytes(self.lock)}.items():
            path = self.output / dest
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def test_complete_bundle_verifies(self):
        self.assertEqual(bundle.verify_bundle(self.output), self.lock)

    def test_changed_reference_rejected(self):
        (self.output / "references/conventions.md").write_text("local drift\n")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            bundle.verify_bundle(self.output)

    def test_missing_reference_rejected(self):
        (self.output / "references/lifecycle.md").unlink()
        with self.assertRaisesRegex(ValueError, "Missing/symlinked"):
            bundle.verify_bundle(self.output)

    def test_source_mapping_cannot_expand(self):
        lock = copy.deepcopy(self.lock)
        lock["files"]["references/conventions.md"]["source"] = "private/note.md"
        with self.assertRaises(ValueError):
            bundle.validate_lock(lock)

    def test_wrong_repository_or_nonimmutable_ref_rejected(self):
        for change in [{"repo": "other/repo"}, {"commit": "main"}, {"schema_version": 2}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                bundle.validate_lock({**self.lock, **change})

    def test_remote_verification_checks_real_source_bytes(self):
        by_source = {bundle.BUNDLE_SOURCES[dest]: data for dest, data in self.payload.items()}
        with patch.object(bundle, "fetch_blob", side_effect=lambda sha, source: by_source[source]) as fetch:
            bundle.verify_bundle(self.output, remote=True)
        self.assertEqual(fetch.call_count, len(self.payload))
        self.assertTrue(all(call.args[0] == self.commit for call in fetch.call_args_list))

    def test_remote_difference_cannot_hide_behind_local_lock(self):
        with patch.object(bundle, "fetch_blob", return_value=b"different upstream bytes\n"):
            with self.assertRaisesRegex(ValueError, "pinned public source"):
                bundle.verify_bundle(self.output, remote=True)

    def test_dirty_checkout_cannot_be_exported_as_a_release(self):
        with patch.object(bundle.subprocess, "check_output", return_value=" M changed.md\n"):
            with self.assertRaisesRegex(ValueError, "clean committed"):
                bundle.export_bundle(ROOT, self.output)

    def test_symlinked_reference_rejected(self):
        path = self.output / "references/conventions.md"
        original = self.output / "original.md"
        path.rename(original)
        path.symlink_to(original)
        with self.assertRaisesRegex(ValueError, "symlinked"):
            bundle.verify_bundle(self.output)


if __name__ == "__main__":
    unittest.main()
