"""Keep the published local HTTP/room contract tied to code, without model calls."""
import ast
import inspect
import re
import tempfile
import unittest
from pathlib import Path

from tests.core.test_group_chat import chat_report
from tests.core.test_server import DummyServer, execute_request
from worktogether.core import Engine
from worktogether.runtime import Runtime
from worktogether.server import WorkTogetherHandler


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "docs/contracts/v1.md"


def canonical_paths(pattern):
    """Normalize only the route grammar the handler uses; unknown syntax fails."""
    pattern = pattern.removeprefix("^").removesuffix("$")
    for parameter in ("([^/]+)", "([a-z][a-z0-9-]{0,63})"):
        pattern = pattern.replace(parameter, "{id}")
    alternative = re.search(r"\(([a-z]+(?:\|[a-z]+)+)\)", pattern)
    if alternative:
        return [path for part in alternative.group(1).split("|")
                for path in canonical_paths(pattern[:alternative.start()] + part
                                            + pattern[alternative.end():])]
    if not re.fullmatch(r"/api/(?:[a-z0-9/-]|\{id\})+", pattern):
        raise AssertionError(f"Unrecognized API route pattern: {pattern!r}")
    return [pattern]


def implementation_routes(source):
    """Read actual path comparisons and regex matches, not a second route registry."""
    tree = ast.parse(source)
    handlers = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                and node.name.startswith("do_")]
    if {node.name for node in handlers} != {"do_GET", "do_POST", "do_OPTIONS"}:
        raise AssertionError("HTTP methods changed; extend the contract checker")
    routes = []
    for handler in handlers:
        if handler.name == "do_OPTIONS":
            continue  # Generic transport response, not a resource route.
        for node in ast.walk(handler):
            patterns = []
            if isinstance(node, ast.Compare) and any(
                    isinstance(value, ast.Name) and value.id == "path"
                    for value in [node.left] + node.comparators):
                if len(node.ops) != 1 or not isinstance(node.ops[0], ast.Eq):
                    raise AssertionError("Unsupported path comparison")
                values = [node.left] + node.comparators
                literals = [value.value for value in values if isinstance(value, ast.Constant)
                            and isinstance(value.value, str)]
                if len(literals) != 1:
                    raise AssertionError("Dynamic route needs an explicit contract check")
                patterns = literals
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and isinstance(node.func.value, ast.Name) and node.func.value.id == "re"
                  and node.func.attr in ("match", "fullmatch") and len(node.args) >= 2
                  and isinstance(node.args[1], ast.Name) and node.args[1].id == "path"):
                if not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
                    raise AssertionError("Dynamic route regex needs an explicit contract check")
                patterns = [node.args[0].value]
            for pattern in patterns:
                routes.extend((handler.name[3:], path) for path in canonical_paths(pattern))
    return routes


def documented_routes(document):
    entries = re.findall(r"^\| (GET|POST) \| `([^`]+)` \|", document, re.MULTILINE)
    return [(method, re.sub(r"\{[a-z_]+\}", "{id}", path)) for method, path in entries]


class RouteContractTests(unittest.TestCase):
    def test_published_routes_exactly_match_the_handler(self):
        actual = implementation_routes(inspect.getsource(WorkTogetherHandler))
        documented = documented_routes(CONTRACT.read_text(encoding="utf-8"))
        self.assertGreater(len(actual), 0)
        self.assertEqual(len(actual), len(set(actual)), "Duplicate implementation route")
        self.assertEqual(len(documented), len(set(documented)), "Duplicate documented route")
        self.assertSetEqual(set(actual), set(documented))

    def test_regex_parameters_and_alternatives_expand(self):
        self.assertEqual(["/api/tasks/{id}/execution-jobs/{id}/recover",
                          "/api/tasks/{id}/planning-jobs/{id}/recover",
                          "/api/tasks/{id}/team-jobs/{id}/recover"], canonical_paths(
                              r"^/api/tasks/([^/]+)/(execution|planning|team)-jobs/([^/]+)/recover$"))
        self.assertEqual(["/api/agents/{id}/profile"], canonical_paths(
            r"/api/agents/([a-z][a-z0-9-]{0,63})/profile"))

    def test_message_request_table_matches_allowed_http_fields(self):
        tree = ast.parse(inspect.getsource(WorkTogetherHandler.do_POST).lstrip())
        branch = next(node for node in ast.walk(tree) if isinstance(node, ast.If)
                      and isinstance(node.test, ast.Name) and node.test.id == "m_messages")
        allowed = next(node.value for node in branch.body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == "allowed_keys"
                               for target in node.targets))
        expected = ast.literal_eval(allowed)
        document = (ROOT / "docs/contracts/chat-v1.md").read_text(encoding="utf-8")
        request_section = document.split("## 用户消息请求", 1)[1].split("## 消息与会话响应", 1)[0]
        fields = re.findall(r"^\| `(\w+)` \|", request_section, re.MULTILINE)
        self.assertEqual(len(fields), len(set(fields)))
        self.assertEqual(expected, set(fields))

    def test_unknown_regex_is_not_silently_ignored(self):
        with self.assertRaises(AssertionError):
            canonical_paths(r"/api/tasks/(.+)/new")

    def test_a_new_route_is_detected_without_updating_a_test_registry(self):
        source = inspect.getsource(WorkTogetherHandler)
        added = source.replace('if path == "/api/health":',
                               'if path == "/api/future-route":\n                pass\n'
                               '            if path == "/api/health":', 1)
        original = set(implementation_routes(source))
        self.assertEqual({("GET", "/api/future-route")},
                         set(implementation_routes(added)) - original)

    def test_new_http_methods_require_contract_review(self):
        source = inspect.getsource(WorkTogetherHandler) + "\n    def do_DELETE(self):\n        pass\n"
        with self.assertRaises(AssertionError):
            implementation_routes(source)


class RoomHttpContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        project = root / "project"
        project.mkdir()
        self.engine = Engine(str(root / "state.sqlite3"))
        self.task = self.engine.task_add(
            self.engine.project_add("fixture", str(project))["id"], "room", "contract")['id']
        self.server = DummyServer(self.engine, Runtime(self.engine, executable="codex"), str(root))
        # No background queue or model process is started in these HTTP tests.
        self.path = f"/api/tasks/{self.task}"

    def request(self, method, suffix, body=None, status=200):
        code, response = execute_request(self.server, method, self.path + suffix, body)
        self.assertEqual(status, code, response)
        return response

    def post_message(self, **overrides):
        return self.request("POST", "/messages", dict(
            {"message_id": "m", "recipient": "codex", "content": "hello"}, **overrides), status=202)

    def test_get_includes_chat_state_and_server_owned_message_fields(self):
        saved = self.post_message()
        self.assertEqual(("user", 1, 1, True),
                         (saved["sender"], saved["round"], saved["rounds"], saved["allow_write"]))
        response = self.request("GET", "/messages")
        self.assertEqual({"messages", "chat"}, set(response))
        self.assertEqual({"allow_write": True, "max_auto_rounds": 2,
                          "auto_rounds_used": 0, "held": 0}, response["chat"])
        self.assertEqual(["codex"], response["messages"][0]["pending_roles"])
        self.assertEqual([], response["messages"][0]["failed_roles"])

    def test_post_cannot_spoof_output_only_fields(self):
        for field, value in {"sender": "antigravity", "intent": "assign", "round": 2,
                             "auto": True, "held": True}.items():
            with self.subTest(field=field):
                self.request("POST", "/messages", {"message_id": "m", "recipient": "codex",
                             "content": "hello", field: value}, status=400)
        self.assertEqual([], self.request("GET", "/messages")["messages"])

    def test_idempotency_does_not_update_rounds_or_write_grant(self):
        saved = self.post_message(allow_write=False, rounds=1)
        repeated = self.post_message(allow_write=True, rounds=2)
        self.assertEqual(saved, repeated)
        self.assertNotIn("allow_write", repeated)
        self.request("POST", "/messages", {"message_id": "m", "recipient": "codex",
                     "content": "different"}, status=409)

    def test_chat_config_bounds_and_legacy_write_default(self):
        for patch in ({"max_auto_rounds": -1}, {"max_auto_rounds": 9},
                      {"max_auto_rounds": True}, {"allow_write": 1}, {"auto_rounds_used": 0}):
            with self.subTest(patch=patch):
                self.request("POST", "/chat-config", patch, status=400)
        with self.engine.transaction() as db:
            task = self.engine._task(db, self.task)
            task["config"].pop("allow_write")
            self.engine._save(db, task, "FIXTURE_LEGACY", {})
        config = self.request("POST", "/chat-config", {})
        self.assertEqual({"task_id", "allow_write", "max_auto_rounds", "auto_rounds_used"}, set(config))
        # Current migration matches new writable sessions; record this explicitly,
        # without granting anything to a real workspace or changing production.
        self.assertTrue(config["allow_write"])
        self.request("POST", "/chat-config", {"allow_write": False})
        self.assertFalse(self.request("POST", "/chat-config", {})["allow_write"])

    def test_auto_message_budget_and_explicit_release_response(self):
        self.request("POST", "/chat-config", {"max_auto_rounds": 0, "allow_write": False})
        self.post_message()
        dispatch = self.engine.chat_dispatch(self.task)
        self.engine.submit(self.task, chat_report(dispatch, data={"messages": [
            {"to": "antigravity", "intent": "ask", "content": "review?"}]}))
        response = self.request("GET", "/messages")
        auto = response["messages"][1]
        self.assertEqual(("codex", "ask", True, "m", "HELD", True),
                         (auto["sender"], auto["intent"], auto["auto"],
                          auto["parent_message_id"], auto["status"], auto["held"]))
        self.assertEqual("AUTO_ROUND_BUDGET", auto["wait_reason"])
        self.assertEqual(1, response["chat"]["held"])
        self.assertEqual({"released": 1}, self.request("POST", "/chat-release", {}))
        self.assertEqual({"released": 0}, self.request("POST", "/chat-release", {}))
        released = self.request("GET", "/messages")
        self.assertNotIn("held", released["messages"][1])
        self.assertEqual("QUEUED", released["messages"][1]["status"])
        self.assertEqual(0, released["chat"]["auto_rounds_used"])
        self.assertEqual({"runs": []}, self.request("GET", "/runs"))

    def test_one_off_read_only_does_not_override_followup_session_grant(self):
        self.post_message(allow_write=False)
        dispatch = self.engine.chat_dispatch(self.task)
        self.assertFalse(dispatch["payload"].get("allow_write", False))
        self.engine.submit(self.task, chat_report(dispatch, data={"messages": [
            {"to": "antigravity", "intent": "assign", "content": "next task"}]}))
        auto = self.request("GET", "/messages")["messages"][1]
        self.assertTrue(auto["allow_write"])
        self.assertEqual(1, self.request("GET", "/messages")["chat"]["auto_rounds_used"])

    def test_round_two_gets_only_bounded_previous_round_replies(self):
        self.post_message(recipient="both", rounds=2, allow_write=False)
        first = self.engine.chat_dispatch_round(self.task)
        self.assertEqual(2, len(first))
        for dispatch in first:
            self.assertEqual([], dispatch["payload"]["peer_replies"])
            self.engine.submit(self.task, chat_report(dispatch, summary="x" * 3000))
        second = self.engine.chat_dispatch_round(self.task)
        self.assertEqual(2, len(second))
        for dispatch in second:
            self.assertEqual(2, dispatch["payload"]["round"])
            self.assertEqual([2000, 2000], [len(r["content"]) for r in dispatch["payload"]["peer_replies"]])

    def test_resolve_default_skip_and_no_automatic_model_launch(self):
        self.post_message()
        dispatch = self.engine.chat_dispatch(self.task)
        self.engine.submit(self.task, chat_report(dispatch, status="BLOCKED"))
        resolved = self.request("POST", "/messages/m/resolve", {})
        self.assertEqual("DELIVERED", resolved["status"])
        self.assertTrue(resolved["replies"][0]["skipped"])
        self.assertEqual({"runs": []}, self.request("GET", "/runs"))

    def test_health_provider_shape_and_features_are_documented(self):
        self.server.provider_status = {"codex": {"available": False, "executable": "fixture"}}
        status, response = execute_request(self.server, "GET", "/api/health")
        self.assertEqual(200, status)
        self.assertEqual({"status", "version", "features", "providers"}, set(response))
        self.assertEqual(self.server.provider_status, response["providers"])
        document = CONTRACT.read_text(encoding="utf-8")
        for feature in response["features"]:
            self.assertIn(f"`{feature}`", document)


if __name__ == "__main__":
    unittest.main()
