"""Regression tests for the CLI argument parser.

A duplicate --db on the `web` subparser used to overwrite a database supplied
before the subcommand, so `--db /tmp/x web` silently used .collab/state.sqlite3.
"""
import unittest

from worktogether.__main__ import parser


class DatabaseArgumentTests(unittest.TestCase):
    def parse(self, argv):
        return parser().parse_args(argv).db

    def test_database_before_subcommand_is_honoured(self):
        self.assertEqual("/tmp/a/state.sqlite3",
                         self.parse(["--db", "/tmp/a/state.sqlite3", "web", "--port", "8765"]))

    def test_database_after_subcommand_wins(self):
        self.assertEqual("/tmp/b/state.sqlite3",
                         self.parse(["web", "--port", "8765", "--db", "/tmp/b/state.sqlite3"]))

    def test_default_database_is_used_when_omitted(self):
        self.assertEqual(".collab/state.sqlite3", self.parse(["web", "--port", "8765"]))

    def test_non_web_subcommands_share_the_same_behaviour(self):
        self.assertEqual("/tmp/c/state.sqlite3",
                         self.parse(["--db", "/tmp/c/state.sqlite3", "doctor", "--project", "x"]))
        self.assertEqual(".collab/state.sqlite3", self.parse(["doctor", "--project", "x"]))


if __name__ == "__main__":
    unittest.main()
