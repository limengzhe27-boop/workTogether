"""Tests for bounded project context, diff and revert helpers."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from worktogether.core import CollabError
from worktogether.workspace import (
    list_project_files,
    project_digest,
    project_diff,
    project_files_snapshot,
    read_project_files,
    revert_project_file,
)


class WorkspaceContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "project"
        self.project.mkdir()
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        (self.project / "README.md").write_text("# Demo project\n\nA tiny fixture.\n", encoding="utf-8")
        (self.project / "tracked.txt").write_text("original\n", encoding="utf-8")
        (self.project / "src").mkdir()
        (self.project / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.project, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
                       cwd=self.project, check=True)

    def test_digest_carries_readme_and_bounded_file_tree(self):
        digest = project_digest(self.project, max_files=2)
        self.assertEqual("project", digest["name"])
        self.assertEqual("README.md", digest["readme_path"])
        self.assertIn("A tiny fixture.", digest["readme"])
        self.assertLessEqual(len(digest["files"]), 2)
        self.assertTrue(digest["files_truncated"])
        self.assertIn("README.md", digest["files"])

    def test_digest_truncates_a_large_readme(self):
        (self.project / "README.md").write_text("x" * 5000, encoding="utf-8")
        digest = project_digest(self.project, max_readme_chars=100)
        self.assertEqual(100, len(digest["readme"]))
        self.assertTrue(digest["readme_truncated"])

    def test_digest_of_missing_directory_is_empty(self):
        self.assertEqual({}, project_digest(self.project / "nope"))

    def test_file_listing_ignores_platform_and_dependency_dirs(self):
        (self.project / ".collab").mkdir()
        (self.project / ".collab" / "state.sqlite3").write_text("x", encoding="utf-8")
        (self.project / "node_modules" / "pkg").mkdir(parents=True)
        (self.project / "node_modules" / "pkg" / "index.js").write_text("x", encoding="utf-8")
        files = list_project_files(self.project, limit=50)
        self.assertNotIn(".collab/state.sqlite3", files)
        self.assertNotIn("node_modules/pkg/index.js", files)
        self.assertIn("src/app.py", files)

    def test_file_listing_respects_limit(self):
        self.assertLessEqual(len(list_project_files(self.project, limit=1)), 1)

    def test_snapshot_reports_sizes(self):
        snapshot = project_files_snapshot(self.project)
        entries = {entry["path"]: entry["size"] for entry in snapshot["files"]}
        self.assertGreater(entries["tracked.txt"], 0)
        self.assertEqual(len(snapshot["files"]), snapshot["total"])

    def test_read_project_files_rejects_escape_paths(self):
        (Path(self.tmp.name) / "outside.txt").write_text("secret", encoding="utf-8")
        results = read_project_files(self.project, ["../outside.txt", "/etc/hosts", "tracked.txt"])
        self.assertEqual(["tracked.txt"], [item["path"] for item in results])

    def test_read_project_files_truncates_large_files(self):
        (self.project / "big.txt").write_text("y" * 5000, encoding="utf-8")
        item = read_project_files(self.project, ["big.txt"], max_bytes_per_file=100)[0]
        self.assertEqual(100, len(item["content"]))
        self.assertTrue(item["truncated"])
        self.assertEqual(5000, item["size_bytes"])

    def test_diff_reports_modified_and_untracked_files(self):
        (self.project / "tracked.txt").write_text("changed\n", encoding="utf-8")
        (self.project / "fresh.txt").write_text("new\n", encoding="utf-8")
        diff = project_diff(self.project)
        self.assertTrue(diff["is_git"])
        status = {entry["path"]: entry["status"] for entry in diff["files"]}
        self.assertEqual("M", status["tracked.txt"])
        self.assertEqual("??", status["fresh.txt"])
        self.assertIn("-original", diff["diff"])

    def test_diff_for_non_git_directory_is_reported_not_raised(self):
        plain = Path(self.tmp.name) / "plain"
        plain.mkdir()
        self.assertEqual({"is_git": False, "files": [], "diff": ""}, project_diff(plain))

    def test_revert_restores_tracked_and_removes_untracked_files(self):
        (self.project / "tracked.txt").write_text("changed\n", encoding="utf-8")
        (self.project / "fresh.txt").write_text("new\n", encoding="utf-8")
        self.assertEqual("checkout", revert_project_file(self.project, "tracked.txt")["mode"])
        self.assertEqual("original\n", (self.project / "tracked.txt").read_text(encoding="utf-8"))
        self.assertEqual("removed_untracked", revert_project_file(self.project, "fresh.txt")["mode"])
        self.assertFalse((self.project / "fresh.txt").exists())

    def test_revert_refuses_paths_outside_the_workspace(self):
        for path in ("../outside.txt", "/etc/hosts", ".collab/state.sqlite3"):
            with self.subTest(path=path):
                with self.assertRaises(CollabError):
                    revert_project_file(self.project, path)

    def test_revert_reports_missing_files(self):
        with self.assertRaises(CollabError) as ctx:
            revert_project_file(self.project, "ghost.txt")
        self.assertEqual("NOT_FOUND", ctx.exception.code)


if __name__ == "__main__":
    unittest.main()
