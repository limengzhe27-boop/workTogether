"""CI contract and read-only bundle checks; no workflow dispatch or model calls."""
import contextlib
import errno
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.check_static import check_static, main
from tests.core import test_loopback_openai


class StaticBundleCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / "worktogether/static"
        self.bundle.mkdir(parents=True)
        self.index = self.bundle / "index.html"
        self.asset = self.bundle / "app.js"
        self.index.write_text('<script src="app.js"></script>')
        self.asset.write_text("fixture")
        self.git("init", "-q")
        self.git("add", "worktogether/static")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True).stdout

    def test_clean_index_matches_without_changing_files_or_index(self):
        before = self.git("ls-files", "--stage")
        self.assertEqual([], check_static(self.root))
        self.assertEqual(before, self.git("ls-files", "--stage"))
        self.assertEqual("fixture", self.asset.read_text())

    def test_modified_tracked_asset_is_rejected(self):
        self.asset.write_text("rebuilt")
        self.assertTrue(any("Asset differs" in issue for issue in check_static(self.root)))
        self.assertEqual("rebuilt", self.asset.read_text())

    def test_new_hash_and_missing_old_asset_are_both_reported(self):
        self.asset.unlink()
        (self.bundle / "app-new.js").write_text("rebuilt")
        issues = check_static(self.root)
        self.assertIn("Missing asset: worktogether/static/app.js", issues)
        self.assertIn("Unstaged/new asset: worktogether/static/app-new.js", issues)

    def test_ignored_and_hidden_new_assets_cannot_hide_from_check(self):
        (self.root / ".gitignore").write_text("*.log\n")
        (self.bundle / "unexpected.log").write_text("unexpected")
        (self.bundle / ".hidden").write_text("unexpected")
        issues = check_static(self.root)
        self.assertTrue(any("unexpected.log" in issue for issue in issues))
        self.assertTrue(any(".hidden" in issue for issue in issues))

    def test_staging_reviewed_rebuilt_assets_satisfies_check(self):
        self.asset.write_text("rebuilt")
        self.git("add", "worktogether/static")
        self.assertEqual([], check_static(self.root))

    def test_unrelated_source_edits_do_not_fail_bundle_only_check(self):
        (self.root / "source.py").write_text("pass")
        self.assertEqual([], check_static(self.root))

    def test_symlink_file_and_directory_are_rejected_without_following(self):
        (self.bundle / "link.js").symlink_to(self.asset)
        (self.bundle / "linked").symlink_to(self.root, target_is_directory=True)
        issues = check_static(self.root)
        self.assertIn("Non-regular asset: worktogether/static/link.js", issues)
        self.assertIn("Symlink directory: worktogether/static/linked", issues)
        self.assertLess(len(issues), 5)  # Do not traverse a link back to the repo.

    def test_missing_index_entry_is_an_error_not_an_empty_success(self):
        self.git("rm", "--cached", "worktogether/static/index.html")
        self.assertTrue(check_static(self.root))

    def test_command_returns_distinct_out_of_sync_and_repository_error_codes(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, main(["--root", str(self.root)]))
            self.asset.write_text("different")
            self.assertEqual(1, main(["--root", str(self.root)]))
            self.assertEqual(2, main(["--root", str(self.root / "missing")]))


class CIWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[2]
        # JSON is a YAML-compatible workflow format, allowing stdlib validation
        # without introducing a YAML package to the dependency-free backend.
        cls.workflow = json.loads((root / ".github/workflows/ci.yml").read_text())

    def test_workflow_is_read_only_pinned_and_does_not_run_privileged_pr_code(self):
        workflow = self.workflow
        self.assertEqual({"push", "pull_request", "workflow_dispatch"}, set(workflow["on"]))
        self.assertEqual({"contents": "read"}, workflow["permissions"])
        self.assertNotIn("secrets.", json.dumps(workflow))
        for job in workflow["jobs"].values():
            self.assertLessEqual(job["timeout-minutes"], 10)
            for step in job["steps"]:
                if "uses" in step:
                    self.assertRegex(step["uses"], r"^actions/[a-z-]+@[a-f0-9]{40}$")
                if step.get("uses", "").startswith("actions/checkout@"):
                    self.assertIs(False, step["with"]["persist-credentials"])

    def test_workflow_runs_all_checks_including_untracked_bundle_detection(self):
        backend = self.workflow["jobs"]["backend"]
        self.assertIn("3.9", backend["strategy"]["matrix"]["python"])
        self.assertEqual("1", backend["env"]["WT_REQUIRE_LOOPBACK_TESTS"])
        self.assertIn("python3 -m unittest discover -s tests -q", [s.get("run") for s in backend["steps"]])
        commands = [s["run"] for s in self.workflow["jobs"]["frontend"]["steps"] if "run" in s]
        self.assertEqual(["npm ci --ignore-scripts --no-audit --no-fund", "npm test", "npm run build",
                          "python3 scripts/check_static.py"], commands)

    def test_required_loopback_check_fails_in_ci_instead_of_silently_skipping(self):
        case = test_loopback_openai.LoopbackOpenAITransportTests("test_probe_discussion_and_delegated_analysis_over_real_http")
        for required in ("0", "1"):
            with self.subTest(required=required), patch.dict(os.environ, {"WT_REQUIRE_LOOPBACK_TESTS": required}), \
                    patch("tests.core.test_loopback_openai.ThreadingHTTPServer", side_effect=PermissionError(errno.EPERM, "fixture")):
                with self.assertRaises(PermissionError if required == "1" else unittest.SkipTest):
                    case.test_probe_discussion_and_delegated_analysis_over_real_http()
