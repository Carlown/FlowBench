import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from tools.marketplace_checksums import ROOT, sync_checksums


class MarketplaceChecksumTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.git("init", "--quiet")
        self.git("config", "core.autocrlf", "true")
        (self.root / "marketplace").mkdir()
        self.index = self.root / "marketplace" / "plugins-index.json"
        # Explicit attributes make this independent of the developer's Git settings.
        (self.root / ".gitattributes").write_text("*.py text eol=lf\n")
        self.plugin = self.root / "marketplace" / "demo.py"
        self.plugin.write_bytes(b"# demo\r\nvalue = 1\r\n")
        self.git("add", ".gitattributes", "marketplace/demo.py")
        self.expected = hashlib.sha256(b"# demo\nvalue = 1\n").hexdigest()
        self.entries = [{"id": "demo", "file": "demo.py", "sha256": "0" * 64}]
        self.save_index()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True)

    def save_index(self):
        self.index.write_text(json.dumps({"plugins": self.entries}), encoding="utf-8")

    def test_check_reports_mismatch_without_writing(self):
        before = self.index.read_bytes()
        self.assertEqual(sync_checksums(self.root), ["demo"])
        self.assertEqual(self.index.read_bytes(), before)

    def test_write_uses_staged_bytes_not_windows_checkout(self):
        self.assertNotEqual(hashlib.sha256(self.plugin.read_bytes()).hexdigest(), self.expected)
        self.assertEqual(sync_checksums(self.root, write=True), ["demo"])
        entry = json.loads(self.index.read_text(encoding="utf-8"))["plugins"][0]
        self.assertEqual(entry["sha256"], self.expected)
        self.assertEqual(sync_checksums(self.root), [])
        before = self.index.read_bytes()
        self.assertEqual(sync_checksums(self.root, write=True), [])
        self.assertEqual(self.index.read_bytes(), before)

    def test_unstaged_edits_do_not_change_published_digest(self):
        self.plugin.write_bytes(b"# changed but not staged\n")
        sync_checksums(self.root, write=True)
        self.assertEqual(json.loads(self.index.read_text())["plugins"][0]["sha256"], self.expected)

    def test_staged_changes_require_new_digest(self):
        sync_checksums(self.root, write=True)
        self.plugin.write_bytes(b"# staged update\n")
        self.git("add", "marketplace/demo.py")
        self.assertEqual(sync_checksums(self.root), ["demo"])

    def test_external_urls_remain_unchanged(self):
        self.entries = [{"id": "external", "file": "https://example.test/plugin.py", "sha256": "a" * 64}]
        self.save_index()
        before = self.index.read_bytes()
        self.assertEqual(sync_checksums(self.root, write=True), [])
        self.assertEqual(self.index.read_bytes(), before)

    def test_missing_staged_file_does_not_partially_write(self):
        self.entries.append({"id": "missing", "file": "missing.py", "sha256": "b" * 64})
        self.save_index()
        before = self.index.read_bytes()
        with self.assertRaisesRegex(ValueError, "git add"):
            sync_checksums(self.root, write=True)
        self.assertEqual(self.index.read_bytes(), before)

    def test_unsafe_paths_are_rejected(self):
        for filename in ("../outside.py", "/outside.py", "C:/outside.py", "..\\outside.py"):
            with self.subTest(filename=filename):
                self.entries[0]["file"] = filename
                self.save_index()
                with self.assertRaisesRegex(ValueError, "Invalid marketplace file"):
                    sync_checksums(self.root, write=True)


class RepositoryMarketplaceTests(unittest.TestCase):
    def test_index_matches_staged_plugin_files(self):
        self.assertEqual(sync_checksums(ROOT), [],
                         "Stage plugins, run tools/marketplace_checksums.py --write, then stage the index")


if __name__ == "__main__":
    unittest.main()
