from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "conversation-evidence.py"
SPEC = importlib.util.spec_from_file_location("conversation_evidence", MODULE_PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def record(timestamp: datetime, role: str, text: str, *, kind: str = "message") -> str:
    return json.dumps(
        {
            "type": kind,
            "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
            "message": {"role": role, "content": [{"type": "text", "text": text}]},
        }
    )


class ConversationEvidenceTests(unittest.TestCase):
    def test_extracts_only_recent_user_and_assistant_text(self) -> None:
        now = datetime(2026, 7, 29, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "session.jsonl"
            path.write_text(
                "\n".join(
                    [
                        record(now - timedelta(days=20), "user", "too old"),
                        record(now - timedelta(hours=2), "user", "Please improve this flow"),
                        record(now - timedelta(hours=1), "toolResult", "private tool payload"),
                        record(now, "assistant", "I fixed the repeated step."),
                    ]
                )
            )
            output = module.build_evidence(root, now=now, days=14)
        self.assertIn("Please improve this flow", output)
        self.assertIn("I fixed the repeated step", output)
        self.assertNotIn("too old", output)
        self.assertNotIn("private tool payload", output)

    def test_unwraps_signal_and_redacts_private_values(self) -> None:
        text = (
            "You are pi, accessed through Signal.\n\n"
            "Signal message from <SIGNAL_E164_EXAMPLE>:\n"
            "Email me at person@example.com from 627 East 6th Street and use "
            "https://example.com/a?token=secret with " + "AKIA" + ("A" * 16)
        )
        result = module.sanitize(text, 1500)
        self.assertTrue(result.startswith("Email me"))
        self.assertIn("[email]", result)
        self.assertIn("[street-address]", result)
        self.assertNotIn("627 East 6th Street", result)
        self.assertIn("https://example.com/a", result)
        self.assertNotIn("token=secret", result)
        self.assertIn("[secret-redacted]", result)
        self.assertNotIn("<SIGNAL_E164_EXAMPLE>", result)

    def test_caps_messages_and_text_length(self) -> None:
        now = datetime(2026, 7, 29, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "session.jsonl"
            path.write_text(
                "\n".join(record(now + timedelta(seconds=i), "user", f"marker-{i} " + "x" * 500) for i in range(20))
            )
            output = module.build_evidence(root, now=now + timedelta(minutes=1), max_messages=10, text_limit=200)
        self.assertNotIn("marker-0", output)
        self.assertIn("marker-19", output)
        message_lines = [line for line in output.splitlines() if " USER: " in line]
        self.assertEqual(len(message_lines), 10)
        self.assertTrue(all(len(line.split(" USER: ", 1)[1]) <= 200 for line in message_lines))

    def test_total_output_is_bounded_and_keeps_newest_evidence(self) -> None:
        now = datetime(2026, 7, 29, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "session.jsonl"
            path.write_text(
                "\n".join(
                    record(now + timedelta(seconds=i), "user", f"entry-{i} " + "word " * 598)
                    for i in range(200)
                )
            )
            output = module.build_evidence(
                root,
                now=now + timedelta(minutes=10),
                max_messages=400,
                text_limit=3000,
            )
        self.assertLess(len(output.encode()), 262_144)
        self.assertIn("entry-199", output)
        self.assertNotIn("entry-0 ", output)

    def test_invalid_json_is_ignored(self) -> None:
        now = datetime(2026, 7, 29, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "session.jsonl").write_text("not json\n" + record(now, "user", "valid message"))
            output = module.build_evidence(root, now=now)
        self.assertIn("valid message", output)

    def test_rotated_daily_logs_take_precedence_and_keep_every_message(self) -> None:
        now = datetime(2026, 7, 29, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sessions = root / "sessions"
            sessions.mkdir()
            (sessions / "session.jsonl").write_text(record(now, "user", "legacy session text"))
            daily = root / "daily"
            daily.mkdir()
            records = [
                {"timestamp": (now + timedelta(seconds=i)).isoformat(), "role": "user", "text": f"daily-{i}"}
                for i in range(450)
            ]
            (daily / "2026-07-29.jsonl").write_text("\n".join(json.dumps(item) for item in records))
            output = module.build_evidence(sessions, now=now, max_messages=10, daily_logs_root=daily)
        self.assertIn("Complete rotated daily log; 450", output)
        self.assertIn("daily-0", output)
        self.assertIn("daily-449", output)
        self.assertNotIn("legacy session text", output)

    def test_build_ignores_invalid_daily_records_after_external_validation(self) -> None:
        now = datetime(2026, 7, 29, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            daily = root / "daily"
            daily.mkdir()
            valid = {"timestamp": now.isoformat(), "role": "assistant", "text": "valid daily reply"}
            (daily / "2026-07-29.jsonl").write_text("bad json\n" + json.dumps(valid))
            output = module.build_evidence(root / "sessions", now=now, daily_logs_root=daily)
        self.assertIn("valid daily reply", output)

    def test_empty_daily_logs_never_fall_back_to_canonical_sessions(self) -> None:
        now = datetime(2026, 7, 29, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sessions = root / "sessions"
            sessions.mkdir()
            (sessions / "session.jsonl").write_text(record(now, "user", "canonical private history"))
            daily = root / "daily"
            daily.mkdir()
            output = module.build_evidence(sessions, now=now, daily_logs_root=daily)
        self.assertIn("Complete rotated daily log; 0", output)
        self.assertNotIn("canonical private history", output)


if __name__ == "__main__":
    unittest.main()
