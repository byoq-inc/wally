import base64
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "migration", Path(__file__).resolve().parents[1] / "scripts/migrate-curated-memory.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "MEMORY.md").write_text("Synthetic memory: café\n")
        self.allow = self.root / "allow"
        self.allow.write_text("MEMORY.md\n")
        self.bundle = self.root / "bundle"
        self.dest = self.root / "restored"

    def export(self):
        m.export(self.source, self.allow, self.bundle)

    def test_roundtrip_and_permissions(self):
        self.export()
        m.restore(self.bundle, self.dest)
        self.assertEqual((self.source / "MEMORY.md").read_bytes(),
                         (self.dest / "MEMORY.md").read_bytes())
        self.assertEqual(self.bundle.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.dest / "MEMORY.md").stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            m.restore(self.bundle, self.dest)
        with self.assertRaises(FileExistsError):
            self.export()

    def test_prohibited_paths(self):
        for path in ("../MEMORY.md", "/MEMORY.md", "auth.json", "sessions/a.jsonl", "x/../MEMORY.md", ".git/MEMORY.md"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                m.relative(path)

    def test_symlink_rejected(self):
        (self.source / "USER.md").symlink_to(self.source / "MEMORY.md")
        self.allow.write_text("USER.md\n")
        with self.assertRaises(ValueError):
            self.export()
        self.assertFalse(self.bundle.exists())

    def test_corrupt_bundle_no_destination(self):
        self.export()
        doc = json.loads(self.bundle.read_text())
        doc["files"][0]["content"] = base64.b64encode(b"changed").decode()
        self.bundle.write_text(json.dumps(doc))
        with self.assertRaises(ValueError):
            m.restore(self.bundle, self.dest)
        self.assertFalse(self.dest.exists())

    def test_duplicate_rejected(self):
        self.export()
        doc = json.loads(self.bundle.read_text())
        doc["files"] *= 2
        self.bundle.write_text(json.dumps(doc))
        with self.assertRaises(ValueError):
            m.restore(self.bundle, self.dest)

    def test_size_bound(self):
        (self.source / "MEMORY.md").write_bytes(b"x" * (m.LIMIT + 1))
        with self.assertRaises(ValueError):
            self.export()


if __name__ == "__main__":
    unittest.main()
