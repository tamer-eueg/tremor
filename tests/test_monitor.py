import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import monitor  # noqa: E402


def spec(required=False):
    return {
        "openapi": "3.0.0",
        "paths": {
            "/widgets": {
                "get": {
                    "parameters": [{
                        "name": "cursor", "in": "query", "required": required,
                        "schema": {"type": "string"},
                    }],
                    "responses": {},
                }
            }
        },
    }


class MonitorConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.originals = {
            name: getattr(monitor, name)
            for name in ("REPO_ROOT", "STATE_DIR", "RUNS_DIR", "PATCHES_DIR", "fetch_spec")
        }
        monitor.REPO_ROOT = str(self.root)
        monitor.STATE_DIR = str(self.root / ".tremor" / "state")
        monitor.RUNS_DIR = str(self.root / ".tremor" / "runs")
        monitor.PATCHES_DIR = str(self.root / ".tremor" / "runs" / "patches")

    def tearDown(self):
        for name, value in self.originals.items():
            setattr(monitor, name, value)
        self.temp.cleanup()

    def write_watchlist(self, entries):
        path = self.root / "tremor-watchlist.json"
        path.write_text(json.dumps(entries))
        return str(path)

    def valid_entry(self):
        return {"name": "provider", "spec_url": "https://api.example.com/openapi.json"}

    def test_rejects_duplicate_names(self):
        entry = self.valid_entry()
        path = self.write_watchlist([entry, copy.deepcopy(entry)])
        with self.assertRaisesRegex(ValueError, "duplicate name"):
            monitor.load_watchlist(path)

    def test_rejects_non_https_url(self):
        entry = self.valid_entry()
        entry["spec_url"] = "http://api.example.com/openapi.json"
        path = self.write_watchlist([entry])
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            monitor.load_watchlist(path)

    def test_rejects_watched_file_outside_repository(self):
        entry = self.valid_entry()
        entry["watched_files"] = ["../secret.py"]
        path = self.write_watchlist([entry])
        with self.assertRaisesRegex(ValueError, "escapes the repository"):
            monitor.load_watchlist(path)

    def test_rejects_state_directory_outside_repository(self):
        monitor.STATE_DIR = str(self.root.parent / "escaped-state")
        with self.assertRaisesRegex(ValueError, "state directory"):
            monitor.run(self.write_watchlist([self.valid_entry()]), quiet=True)

    def test_first_run_then_breaking_change(self):
        path = self.write_watchlist([self.valid_entry()])
        snapshots = iter([spec(required=False), spec(required=True)])
        monitor.fetch_spec = lambda _url: next(snapshots)

        first = monitor.run(path, quiet=True)[0]
        second = monitor.run(path, quiet=True)[0]

        self.assertEqual(first["status"], "baseline_established")
        self.assertEqual(second["status"], "checked")
        self.assertEqual(second["breaking_count"], 1)
        self.assertEqual(second["changes"][0]["kind"], "parameter_now_required")
        self.assertTrue((self.root / ".tremor" / "state" / "provider.json.gz").exists())
        self.assertEqual(len(list((self.root / ".tremor" / "runs").glob("provider_*.json"))), 1)

    def test_operational_failure_has_distinct_exit_code(self):
        self.assertEqual(monitor.result_exit_code([{"status": "checked", "breaking_count": 0}]), 0)
        self.assertEqual(monitor.result_exit_code([{"status": "checked", "breaking_count": 1}]), 1)
        self.assertEqual(monitor.result_exit_code([{"status": "fetch_failed"}]), 2)


class DistributionTests(unittest.TestCase):
    def test_composite_action_has_safe_customer_defaults(self):
        action = (ROOT / "action.yml").read_text()
        self.assertIn("using: composite", action)
        self.assertIn("TREMOR_REPO_ROOT: ${{ github.workspace }}", action)
        self.assertIn("default: .tremor/state", action)
        self.assertIn("default: .tremor/runs", action)
        self.assertIn("fail-on-breaking", action)
        self.assertIn("patches-applied", action)
        self.assertNotIn("pull-requests: write", action)
        self.assertNotIn("merge", action.lower())

    def test_review_workflow_opens_but_cannot_merge(self):
        workflow = (ROOT / "examples" / "tremor-review-pr.workflow.yml").read_text()
        self.assertIn("pull-requests: write", workflow)
        self.assertIn("gh pr create", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertNotIn("gh pr merge", workflow)


if __name__ == "__main__":
    unittest.main()
