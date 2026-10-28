import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from durable_lifecycle import DurableLifecycleController, LifecycleConflict


class DurableLifecycleControllerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="durable-lifecycle-")
        self.base = Path(self.temporary.name)
        self.runtime = self.base / "runtime"; self.runtime.mkdir(mode=0o700)
        self.registry = self.base / "registry"; self.registry.mkdir(mode=0o700)
        self.controller = DurableLifecycleController(
            self.base / "state", self.runtime, self.registry, lease_seconds=5,
        )
        self.children = []

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.kill(); child.wait()
        self.temporary.cleanup()

    def worker(self, generation="generation-123"):
        run = self.runtime / generation; run.mkdir(mode=0o700)
        (run / "stderr.log").write_text("preserved output", encoding="utf-8")
        (run / "exit.status").write_text("0", encoding="ascii")
        artifact = self.registry / (generation + ".json")
        artifact.write_text("{}", encoding="utf-8"); artifact.chmod(0o600)
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.children.append(child)
        record = self.controller.register(
            "private-logical-key", "logical-session-123", generation,
            child.pid, self.controller.proc_start_ticks(child.pid), run,
            [run / "stderr.log", run / "exit.status", artifact], now=100,
        )
        return child, run, artifact, record

    def test_active_lease_cannot_be_retired_or_replaced(self):
        child, _, _, _ = self.worker()
        self.assertFalse(self.controller.retire("private-logical-key", "generation-123", now=101))
        self.assertIsNone(child.poll())
        with self.assertRaises(LifecycleConflict):
            self.controller.register(
                "private-logical-key", "logical-session-123", "generation-456",
                child.pid, self.controller.proc_start_ticks(child.pid), self.runtime / "generation-123",
            )

    def test_terminal_retirement_never_signals_pid_replacement(self):
        child, run, artifact, _ = self.worker()
        path = self.controller._record_path("private-logical-key")
        record = json.loads(path.read_text())
        record["worker_start_ticks"] += 1
        self.controller._write(path, record)
        self.controller.mark_terminal("private-logical-key", "generation-123")
        self.assertTrue(self.controller.retire("private-logical-key", "generation-123"))
        self.assertIsNone(child.poll())
        self.assertFalse(run.exists()); self.assertFalse(artifact.exists())

    def test_terminal_retirement_kills_exact_worker_and_preserves_output(self):
        child, run, artifact, _ = self.worker()
        self.controller.mark_terminal("private-logical-key", "generation-123")
        self.assertTrue(self.controller.retire("private-logical-key", "generation-123"))
        child.wait(timeout=3)
        self.assertFalse(run.exists()); self.assertFalse(artifact.exists())
        archive = self.controller.archive / "generation-123"
        self.assertEqual((archive / "stderr.log").read_text(), "preserved output")
        self.assertEqual(self.controller.inspect("private-logical-key")["state"], "retired")

    def test_restart_reconciles_expired_lease_without_raw_owner_key_on_disk(self):
        child, run, _, _ = self.worker()
        restarted = DurableLifecycleController(
            self.base / "state", self.runtime, self.registry, lease_seconds=5,
        )
        retired = restarted.reconcile(now=106)
        self.assertEqual(len(retired), 1)
        child.wait(timeout=3)
        self.assertFalse(run.exists())
        record_text = next(restarted.records.glob("*.json")).read_text()
        self.assertNotIn("private-logical-key", record_text)
        self.assertNotIn("private-logical-key", next(restarted.records.glob("*.json")).name)

    def test_restart_reconciles_proven_lost_controller_before_lease_expiry(self):
        child, _, _, _ = self.worker()
        path = self.controller._record_path("private-logical-key")
        record = json.loads(path.read_text())
        record["controller_start_ticks"] += 1
        self.controller._write(path, record)
        restarted = DurableLifecycleController(
            self.base / "state", self.runtime, self.registry, lease_seconds=5,
        )
        self.assertEqual(len(restarted.reconcile(now=101)), 1)
        child.wait(timeout=3)

    def test_retirement_handles_already_removed_run_directory(self):
        child, run, artifact, _ = self.worker()
        child.terminate(); child.wait(timeout=3)
        for item in run.iterdir():
            item.unlink()
        run.rmdir()
        self.controller.mark_terminal("private-logical-key", "generation-123")
        self.assertTrue(self.controller.retire("private-logical-key", "generation-123"))
        self.assertFalse(artifact.exists())
        self.assertEqual(self.controller.inspect("private-logical-key")["state"], "retired")

    def test_restart_finishes_retiring_record_with_missing_run_directory(self):
        child, run, artifact, _ = self.worker()
        child.terminate(); child.wait(timeout=3)
        for item in run.iterdir():
            item.unlink()
        run.rmdir()
        path = self.controller._record_path("private-logical-key")
        record = json.loads(path.read_text())
        record["state"] = "retiring"
        self.controller._write(path, record)
        restarted = DurableLifecycleController(
            self.base / "state", self.runtime, self.registry, lease_seconds=5,
        )
        self.assertEqual(len(restarted.reconcile(now=106)), 1)
        self.assertFalse(artifact.exists())
        self.assertEqual(restarted.inspect("private-logical-key")["state"], "retired")
        self.assertEqual(restarted.reconcile(now=107), [])

    def test_heartbeat_extends_lease_across_controller_instance(self):
        child, _, _, _ = self.worker()
        self.controller.heartbeat("private-logical-key", "generation-123", now=103)
        restarted = DurableLifecycleController(
            self.base / "state", self.runtime, self.registry, lease_seconds=5,
        )
        self.assertEqual(restarted.reconcile(now=107), [])
        self.assertIsNone(child.poll())


if __name__ == "__main__":
    unittest.main()
