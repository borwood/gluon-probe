"""The host-integration example must keep running and stay consistent with the shipped schema."""
from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

import host_integration  # noqa: E402


class HostIntegrationExampleTests(unittest.TestCase):
    def test_example_runs_and_reports_replay_and_diff(self):
        with contextlib.redirect_stdout(io.StringIO()):
            summary = host_integration.main()
        self.assertEqual(summary["replayed_on"], "s2")
        self.assertEqual(summary["added"], ["Epsilon"])
        self.assertEqual(summary["removed"], [("Alpha", "retired")])
        self.assertTrue(summary["configuration_hash"].startswith("sha256:"))


if __name__ == "__main__":
    unittest.main()
