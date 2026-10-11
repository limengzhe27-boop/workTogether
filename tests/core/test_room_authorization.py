"""Room audiences and write grants cannot grow through delegation or stale launches."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.core.test_group_chat import chat_report
from tests.core.test_server import DummyServer, execute_request
from worktogether.agents import AgentRegistry
from worktogether.adapters import MockAdapter
from worktogether.antigravity import AntigravityRuntime
from worktogether.core import CollabError, Engine, encode
from worktogether.chat import ChatQueue
from worktogether.harness import HarnessRuntime
from worktogether.runtime import Runtime


class RoomAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        project = root / "project"
        project.mkdir()
        self.engine = Engine(str(root / "state.sqlite3"))
        self.project = self.engine.project_add("fixture", str(project))
        self.task = self.engine.task_add(self.project["id"], "room", "coordinate")["id"]
        self.registry = AgentRegistry(self.engine)

    def root_round(self, **options):
        self.engine.message_add(self.task, "root", "both", "coordinate", **options)
        return self.engine.chat_dispatch_round(self.task)

    def reply(self, dispatch, target=None, intent="assign"):
        data = {"messages": [{"to": target, "intent": intent, "content": "follow up"}]} if target else {}
        self.engine.submit(self.task, chat_report(dispatch, data=data))

    def delegate(self, **options):
        first, peer = self.root_round(**options)
        self.reply(peer)
        self.reply(first, "antigravity")
        return self.engine.messages(self.task)[-1]

    def test_new_tasks_and_absent_permission_are_read_only(self):
        self.assertFalse(self.engine.chat_state(self.task)["allow_write"])
        with self.assertRaises(CollabError) as caught:
            self.engine.message_add(self.task, "illegal", "codex", "write", allow_write=True)
        self.assertEqual("APPROVAL_REQUIRED", caught.exception.code)
        self.assertEqual([], self.engine.messages(self.task))

    def test_unselected_ready_peer_is_rejected_for_all_intents(self):
        self.engine.message_add(self.task, "root", "codex", "only you")
        dispatch = self.engine.chat_dispatch(self.task)
        self.assertEqual(["codex"], dispatch["payload"]["room_members"])
        for intent in ("ask", "assign", "claim", "report"):
            with self.subTest(intent=intent), self.assertRaises(CollabError):
                self.reply(dispatch, "antigravity", intent)
        self.assertEqual(1, len(self.engine.messages(self.task)))

    def test_newly_enabled_member_cannot_join_existing_chain(self):
        self.delegate()
        self.registry.register({"id": "helper", "name": "Helper", "type": "cli", "base_agent": "codex"})
        follow = self.engine.chat_dispatch(self.task)
        self.assertEqual(["antigravity", "codex"], follow["payload"]["room_members"])
        with self.assertRaises(CollabError):
            self.reply(follow, "helper")
        self.reply(follow, "codex", "ask")
        self.assertEqual(["antigravity", "codex"], self.engine.messages(self.task)[-1]["room_members"])

    def test_single_message_read_only_survives_multiple_handoffs(self):
        self.engine.chat_config(self.task, {"allow_write": True, "max_auto_rounds": 3})
        auto = self.delegate(allow_write=False)
        self.assertFalse(auto.get("allow_write", False))
        follow = self.engine.chat_dispatch(self.task)
        self.assertFalse(follow["payload"].get("allow_write", False))
        self.reply(follow, "codex")
        last = self.engine.chat_dispatch(self.task)
        self.assertFalse(last["payload"].get("allow_write", False))

    def test_an_authorized_reading_peer_can_delegate_within_original_grant(self):
        self.engine.chat_config(self.task, {"allow_write": True})
        writer, reader = self.root_round()
        self.assertFalse(reader["payload"].get("allow_write", False))
        self.reply(writer)
        self.reply(reader, "codex")
        self.assertTrue(self.engine.chat_dispatch(self.task)["payload"]["allow_write"])

    def test_queued_write_is_not_revived_by_reenabling(self):
        self.engine.chat_config(self.task, {"allow_write": True})
        self.engine.message_add(self.task, "old", "codex", "queued")
        self.engine.chat_config(self.task, {"allow_write": False})
        self.engine.chat_config(self.task, {"allow_write": True})
        self.assertFalse(self.engine.messages(self.task)[0]["write_authorized"])
        dispatch = self.engine.chat_dispatch(self.task)
        self.assertFalse(dispatch["payload"].get("allow_write", False))
        self.reply(dispatch)
        self.engine.message_add(self.task, "new", "codex", "fresh authorization")
        self.assertTrue(self.engine.chat_dispatch(self.task)["payload"]["allow_write"])

    def test_held_release_does_not_restore_revoked_write(self):
        self.engine.chat_config(self.task, {"allow_write": True, "max_auto_rounds": 0})
        held = self.delegate()
        self.assertTrue(held["allow_write"])
        self.assertEqual("HELD", held["status"])
        self.engine.chat_config(self.task, {"allow_write": False})
        self.engine.chat_config(self.task, {"allow_write": True})
        self.engine.chat_release(self.task)
        self.assertFalse(self.engine.chat_dispatch(self.task)["payload"].get("allow_write", False))

    def test_in_flight_result_cannot_regrant_write_after_revocation(self):
        self.engine.chat_config(self.task, {"allow_write": True})
        writer, reader = self.root_round()
        self.engine.chat_config(self.task, {"allow_write": False})
        self.engine.chat_config(self.task, {"allow_write": True})
        self.reply(reader)
        self.reply(writer, "antigravity")
        self.assertFalse(self.engine.messages(self.task)[-1].get("allow_write", False))

    def test_prepared_write_cannot_launch_after_revocation_for_each_cli(self):
        root = Path(__file__).parent
        cases = [(Runtime, "codex", "fake_codex.py"),
                 (AntigravityRuntime, "antigravity", "fake_agy.py"),
                 (HarnessRuntime, "deepseek-harness", "fake_harness.py")]
        for cls, role, fixture in cases:
            with self.subTest(role=role):
                task = self.engine.task_add(self.project["id"], role, "permission race")["id"]
                if role == "deepseek-harness":
                    # Pin the test executable instead of depending on an installed app.
                    with self.engine.transaction() as db:
                        row = db.execute("SELECT body FROM agents WHERE id=?", (role,)).fetchone()
                        agent = json.loads(row[0])
                        agent.update(enabled=True, executable=str(root / fixture))
                        db.execute("UPDATE agents SET body=? WHERE id=?", (encode(agent), role))
                self.engine.chat_config(task, {"allow_write": True})
                self.engine.message_add(task, "m", role, "write")
                dispatch = self.engine.chat_dispatch(task)
                self.assertTrue(dispatch["payload"]["allow_write"])
                runtime = cls(self.engine, str(root / fixture))
                run, reserved = runtime._reserve(task, dispatch["id"], 30)
                self.engine.chat_config(task, {"allow_write": False})
                self.engine.chat_config(task, {"allow_write": True})
                with patch("worktogether.workspace.file_digests", return_value={}), \
                        patch("worktogether.runtime.subprocess.Popen") as spawn:
                    result = runtime.execute_reserved(run, reserved)
                spawn.assert_not_called()
                self.assertEqual("FAILED", result["status"])
                self.assertEqual("PERMISSION_REVOKED", result["error_code"])
                self.assertEqual("BLOCKED", self.engine.messages(task)[0]["status"])
                self.engine.message_resolve(task, "m", "skip")

    def test_legacy_auto_message_without_a_scope_requires_review(self):
        self.delegate()
        with self.engine.transaction() as db:
            row = db.execute("SELECT id,body FROM messages WHERE task_id=? ORDER BY rowid DESC LIMIT 1",
                             (self.task,)).fetchone()
            message = json.loads(row["body"])
            message.pop("room_members")
            db.execute("UPDATE messages SET body=? WHERE task_id=? AND id=?",
                       (encode(message), self.task, row["id"]))
        self.assertEqual([], self.engine.chat_dispatch_round(self.task))
        auto = self.engine.messages(self.task)[-1]
        self.assertEqual("ROOM_SCOPE_CHANGED", auto["wait_reason"])
        self.engine.message_resolve(self.task, auto["id"], "skip")
        self.assertEqual("DELIVERED", self.engine.messages(self.task)[-1]["status"])

    def test_prepared_read_rechecks_membership_before_launch(self):
        self.engine.message_add(self.task, "m", "codex", "read only")
        dispatch = self.engine.chat_dispatch(self.task)
        runtime = Runtime(self.engine, str(Path(__file__).parent / "fake_codex.py"))
        run, reserved = runtime._reserve(self.task, dispatch["id"], 30)
        # Emulate membership invalidation by an external importer between reserve
        # and launch; the normal registry itself refuses active-member changes.
        with self.engine.transaction() as db:
            db.execute("UPDATE agents SET body=json_set(body,'$.enabled',json('false')) WHERE id='codex'")
        with patch("worktogether.workspace.file_digests", return_value={}), \
                patch("worktogether.runtime.subprocess.Popen") as spawn:
            result = runtime.execute_reserved(run, reserved)
        spawn.assert_not_called()
        self.assertEqual("ROOM_SCOPE_CHANGED", result["error_code"])
        self.assertEqual("BLOCKED", self.engine.messages(self.task)[0]["status"])

    def test_scope_and_epoch_cannot_be_forged_through_http(self):
        server = DummyServer(self.engine, Runtime(self.engine), self.tmp.name)
        for field, value in {"room_members": ["codex", "antigravity"], "write_epoch": 10,
                             "write_authorized": True}.items():
            with self.subTest(field=field):
                code, _ = execute_request(server, "POST", f"/api/tasks/{self.task}/messages", {
                    "message_id": "m", "recipient": "codex", "content": "hello", field: value})
                self.assertEqual(400, code)
        self.assertEqual([], self.engine.messages(self.task))

    def test_available_members_are_limited_to_the_configured_team(self):
        self.registry.register({"id": "helper", "name": "Helper", "type": "cli", "base_agent": "codex"})
        self.engine.configure_team(self.task, ["codex", "antigravity"])
        self.assertEqual(["antigravity", "codex"], self.engine.chat_state(self.task)["available_members"])

    def test_informational_broadcast_has_no_pending_roles_and_ask_all_is_rejected(self):
        first, peer = self.root_round()
        self.reply(peer)
        with self.assertRaises(CollabError):
            self.reply(first, "all", "ask")
        self.reply(first, "all", "claim")
        info = self.engine.messages(self.task)[-1]
        self.assertEqual([], info["pending_roles"])
        self.assertEqual("DELIVERED", info["status"])

    def test_formal_code_waits_for_chat_writer_and_blocked_writer(self):
        other = self.engine.task_add(self.project["id"], "formal", "implement")["id"]
        adapter = MockAdapter()
        for _ in range(3):
            dispatch = self.engine.dispatch(other)["dispatch"]
            self.engine.submit(other, adapter.prepare(dispatch, self.engine.template(other, dispatch["id"])))
        self.engine.chat_config(self.task, {"allow_write": True})
        self.engine.message_add(self.task, "write", "codex", "write")
        writer = self.engine.chat_dispatch(self.task)
        self.assertTrue(writer["payload"]["allow_write"])
        self.assertIsNone(self.engine.dispatch(other)["dispatch"])
        self.engine.submit(self.task, chat_report(writer, status="BLOCKED"))
        self.assertIsNone(self.engine.dispatch(other)["dispatch"])
        self.engine.message_resolve(self.task, "write", "skip")
        self.assertEqual("CODE", self.engine.dispatch(other)["dispatch"]["kind"])

    def test_chat_stays_read_only_while_formal_verification_owns_project(self):
        other = self.engine.task_add(self.project["id"], "formal", "implement")["id"]
        adapter = MockAdapter()
        for _ in range(4):
            dispatch = self.engine.dispatch(other)["dispatch"]
            self.engine.submit(other, adapter.prepare(dispatch, self.engine.template(other, dispatch["id"])))
        self.assertEqual("VERIFY", self.engine.dispatch(other)["dispatch"]["kind"])
        self.engine.chat_config(self.task, {"allow_write": True})
        self.engine.message_add(self.task, "m", "codex", "review")
        self.assertFalse(self.engine.chat_dispatch(self.task)["payload"].get("allow_write", False))

    def test_two_round_room_and_peer_followup_over_http_with_fake_clis(self):
        root = Path(__file__).parent
        codex = Runtime(self.engine, str(root / "fake_codex.py"))
        queue = ChatQueue(self.engine, codex, AntigravityRuntime(self.engine, str(root / "fake_agy.py")))
        server = DummyServer(self.engine, codex, self.tmp.name)
        path = f"/api/tasks/{self.task}/messages"
        code, _ = execute_request(server, "POST", path, {
            "message_id": "root", "recipient": "codex,antigravity", "content": "discuss",
            "rounds": 2, "allow_write": False})
        self.assertEqual(202, code)
        with patch.dict("os.environ", {"WT_FAKE_MODE": "peer"}):
            for _ in range(3):
                queue.once()
        code, response = execute_request(server, "GET", path)
        self.assertEqual(200, code)
        self.assertEqual(3, len(response["messages"]))
        self.assertTrue(all(m["status"] == "DELIVERED" for m in response["messages"]))
        self.assertEqual(4, len(response["messages"][0]["replies"]))
        for message in response["messages"]:
            self.assertEqual({"codex", "antigravity"}, set(message["room_members"]))
            self.assertFalse(message["write_authorized"])
        self.assertEqual(5, len(codex.list(self.task)["runs"]))


if __name__ == "__main__":
    unittest.main()
