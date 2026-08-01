from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).parents[1] / "conversation-log-lifecycle.py"
SPEC = importlib.util.spec_from_file_location("conversation_log_lifecycle", MODULE_PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def memory_manifest(path: Path) -> Path:
    hashes = {"USER.md": "1" * 64, "MEMORY.md": "2" * 64, "SKILL_IDEAS.md": "3" * 64}
    path.write_text("".join(f"{digest}  {name}\n" for name, digest in hashes.items()))
    return path


class ConversationLogLifecycleTests(unittest.TestCase):
    def rotate_one(self, root: Path) -> tuple[Path, Path]:
        source = root / "running"
        source.mkdir()
        (source / "2026-07-29.jsonl").write_text('{"timestamp":"2026-07-29T00:00:00Z","role":"user","text":"message"}\n')
        pending = root / "state" / "run-1"
        self.assertEqual(module.rotate(source, pending), 1)
        return source, pending

    def test_success_cleanup_requires_ack_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, pending = self.rotate_one(root)
            ack = root / "state" / "success.json"
            marker = root / "state" / "consumed.json"
            module.acknowledge(pending, ack, "run-1", memory_manifest(root / "after.sha256"))
            module.consume(pending, ack, marker, "run-1")
            self.assertFalse(pending.exists())
            marker_value = json.loads(marker.read_text())
            self.assertEqual(marker_value["status"], "consumed")
            self.assertRegex(marker_value["pending_manifest_sha256"], r"\A[0-9a-f]{64}\Z")
            self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)
            module.consume(pending, ack, marker, "run-1")
            self.assertFalse(pending.exists())

    def test_validation_failure_retains_pending(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, pending = self.rotate_one(root)
            log = pending / "2026-07-29.jsonl"
            log.write_text("tampered\n")
            with self.assertRaises(RuntimeError):
                module.acknowledge(pending, root / "ack", "run-1", memory_manifest(root / "after.sha256"))
            self.assertTrue(log.is_file())

    def test_malformed_daily_record_fails_rotation_without_data_loss(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "running"
            source.mkdir()
            log = source / "2026-07-29.jsonl"
            log.write_text('{"timestamp":"2026-07-29T00:00:00Z","role":"user","text":"ok","attachment":"forbidden"}\n')
            with self.assertRaises(RuntimeError):
                module.rotate(source, root / "pending")
            self.assertTrue(log.is_file())
            self.assertFalse((root / "pending").exists())

    def test_worker_failure_without_ack_retains_pending(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, pending = self.rotate_one(root)
            with self.assertRaises(RuntimeError):
                module.consume(pending, root / "missing-ack", root / "marker", "run-1")
            self.assertTrue((pending / "2026-07-29.jsonl").is_file())

    def test_malformed_acknowledgements_retain_pending(self) -> None:
        malformed = (
            b"not json\n",
            b'{"version":1,"run_id":"run-1","status":"failure"}\n',
            b'{"version":1,"run_id":"other","status":"success","memory_sha256":{}}\n',
        )
        for content in malformed:
            with self.subTest(content=content):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    _, pending = self.rotate_one(root)
                    ack = root / "ack"
                    ack.write_bytes(content)
                    with self.assertRaises(RuntimeError):
                        module.consume(pending, ack, root / "marker", "run-1")
                    self.assertTrue(pending.is_dir())

    def test_acknowledgement_with_extra_fields_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, pending = self.rotate_one(root)
            ack = root / "state" / "success.json"
            marker = root / "state" / "consumed.json"
            module.acknowledge(pending, ack, "run-1", memory_manifest(root / "after.sha256"))
            value = json.loads(ack.read_text())
            value["unvalidated"] = True
            ack.write_text(json.dumps(value))
            os.chmod(ack, 0o600)
            with self.assertRaises(RuntimeError):
                module.consume(pending, ack, marker, "run-1")
            self.assertTrue(pending.is_dir())

    def test_idempotent_reconciliation_revalidates_ack_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, pending = self.rotate_one(root)
            ack = root / "state" / "success.json"
            marker = root / "state" / "consumed.json"
            module.acknowledge(pending, ack, "run-1", memory_manifest(root / "after.sha256"))
            original = pending / "2026-07-29.jsonl"
            saved_log = original.read_bytes()
            saved_manifest = (pending / ".manifest.json").read_bytes()
            module.consume(pending, ack, marker, "run-1")

            pending.mkdir(mode=0o700)
            original.write_bytes(saved_log)
            os.chmod(original, 0o600)
            (pending / ".manifest.json").write_bytes(saved_manifest)
            os.chmod(pending / ".manifest.json", 0o600)
            ack.write_text("{}")
            os.chmod(ack, 0o600)
            with self.assertRaises(RuntimeError):
                module.consume(pending, ack, marker, "run-1")
            self.assertTrue(pending.is_dir())

    def test_private_modes_are_required_during_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, pending = self.rotate_one(root)
            os.chmod(pending / "2026-07-29.jsonl", 0o640)
            with self.assertRaises(RuntimeError):
                module.validate_pending(pending)
            self.assertTrue(pending.is_dir())

    def test_rotate_is_private_atomic_and_does_not_capture_later_messages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, pending = self.rotate_one(root)
            self.assertEqual(stat.S_IMODE(pending.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((pending / "2026-07-29.jsonl").stat().st_mode), 0o600)
            later = source / "2026-07-30.jsonl"
            later.write_text("later\n")
            self.assertEqual(module.rotate(source, pending), 0)
            self.assertTrue(later.is_file())
            self.assertFalse((pending / "2026-07-30.jsonl").exists())

    def test_rotation_failure_restores_moved_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "running"
            source.mkdir()
            log = source / "2026-07-29.jsonl"
            log.write_text(
                '{"timestamp":"2026-07-29T22:00:00-04:00",'
                '"role":"user","text":"message"}\n'
            )
            with mock.patch.object(module, "_atomic_bytes", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    module.rotate(source, root / "pending")
            self.assertTrue(log.is_file())
            self.assertFalse((root / "pending").exists())

    def test_rotate_rejects_symlinked_and_unbounded_logs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "running"
            source.mkdir()
            target = root / "private"
            target.write_text("do not move")
            os.symlink(target, source / "2026-07-29.jsonl")
            with self.assertRaises(RuntimeError):
                module.rotate(source, root / "pending")
            self.assertTrue(target.is_file())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "running"
            source.mkdir()
            for index in range(module.MAX_LOG_FILES + 1):
                (source / f"2026-07-29-{index}.jsonl").write_text("x")
            with self.assertRaises(RuntimeError):
                module.rotate(source, root / "pending")
            self.assertEqual(len(list(source.glob("*.jsonl"))), module.MAX_LOG_FILES + 1)

    def test_memory_manifest_must_be_complete_and_well_formed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, pending = self.rotate_one(root)
            invalid = root / "after.sha256"
            invalid.write_text(f"{'1' * 64}  USER.md\n")
            with self.assertRaises(RuntimeError):
                module.acknowledge(pending, root / "ack", "run-1", invalid)
            self.assertTrue(pending.is_dir())


if __name__ == "__main__":
    unittest.main()
