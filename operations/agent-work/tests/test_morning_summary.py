from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "morning-summary.py"
SPEC = importlib.util.spec_from_file_location("morning_summary", MODULE_PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class MorningSummaryNightIdTests(unittest.TestCase):
    def test_accepts_scheduled_night(self) -> None:
        self.assertTrue(module.current_night_id("2026-07-28", "2026-07-28"))

    def test_accepts_bounded_manual_marker(self) -> None:
        self.assertTrue(module.current_night_id("2026-07-28-manual-170919", "2026-07-28"))

    def test_rejects_stale_or_malformed_markers(self) -> None:
        rejected = [
            "2026-07-27",
            "2026-07-28-manual-",
            "2026-07-28-manual-bad marker",
            "2026-07-280-manual-start",
            "manual-2026-07-28",
        ]
        for value in rejected:
            with self.subTest(value=value):
                self.assertFalse(module.current_night_id(value, "2026-07-28"))


if __name__ == "__main__":
    unittest.main()
