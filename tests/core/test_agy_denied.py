import json
import tempfile
import unittest
from pathlib import Path
from worktogether.antigravity import AntigravityRuntime
from worktogether.core import CollabError


class DeniedResultTests(unittest.TestCase):
    def test_denial_is_persisted_with_task_reason_and_command(self):
        from unittest.mock import patch
        from worktogether.core import Engine
        from worktogether.adapters import MockAdapter
        with tempfile.TemporaryDirectory() as tmp:
            e = Engine(Path(tmp) / 'state.sqlite3')
            p = e.project_add('fixture', tmp)
            task = e.task_add(p['id'], 'fixture', 'fixture')['id']
            d = e.dispatch(task)['dispatch']
            e.submit(task, MockAdapter().prepare(d, e.template(task, d['id'])))
            d = e.dispatch(task)['dispatch']
            runtime = AntigravityRuntime(e, str(Path(__file__).with_name('fake_agy.py').resolve()))
            with patch.dict('os.environ', {'WT_AGY_FIXTURE': 'denied'}):
                run = runtime.run(task, d['id'], 30)
            self.assertEqual('PERMISSION_DENIED', run['error_code'])
            self.assertEqual(['python3 -m unittest -v'], run['error_details']['denied_commands'])
            self.assertEqual({'input_tokens': 5}, run['usage'])
            self.assertEqual('PERMISSION_DENIED', e.show(task)['block_reason'])
            self.assertEqual('FAILED', runtime.list(task)['runs'][0]['status'])

    def test_success_with_denial_or_empty_report_is_not_accepted(self):
        for denied, code in [(True, 'PERMISSION_DENIED'), (False, 'INVALID_REPORT')]:
            with self.subTest(denied=denied), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                result = {'conversation_id': 'fixture', 'status': 'SUCCESS', 'response': ''}
                if denied:
                    result['denied_actions'] = [{'action': 'command'}]
                (root / 'events.jsonl').write_text('\n'.join(json.dumps(event) for event in [
                    {'event': 'init', 'conversation_id': 'fixture'}, {'event': 'result', 'result': result}]))
                with self.assertRaises(CollabError) as error:
                    AntigravityRuntime.read_result(None, root)
                self.assertEqual(code, error.exception.code)
                self.assertFalse((root / 'result.json').exists())

    def _denied_run(self, root, dispatch_kind):
        """Write an events file where a command was denied but the model answered."""
        result = {'conversation_id': 'fixture', 'status': 'SUCCESS',
                  'response': json.dumps({"summary": "我的判断", "basis": ["读到的文件"]}),
                  'denied_actions': [{'action': 'command'}]}
        (root / 'events.jsonl').write_text('\n'.join(json.dumps(event) for event in [
            {'event': 'init', 'conversation_id': 'fixture'}, {'event': 'result', 'result': result}]))
        return AntigravityRuntime.read_result(None, root, {"kind": dispatch_kind})

    def test_a_denied_command_does_not_discard_a_usable_chat_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session, completed, report, usage = self._denied_run(root, 'CHAT')
            self.assertEqual('fixture', session)
            # The answer survives, and the denial is disclosed rather than hidden.
            self.assertIn('我的判断', report['summary'])
            self.assertIn('权限拒绝', report['summary'])
            self.assertTrue((root / 'result.json').exists())

    def test_a_denied_command_still_fails_a_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(CollabError) as error:
                self._denied_run(root, 'VERIFY')
            self.assertEqual('PERMISSION_DENIED', error.exception.code)

    def test_chat_is_steered_away_from_shell_commands(self):
        from worktogether.core import Engine
        with tempfile.TemporaryDirectory() as tmp:
            runtime = AntigravityRuntime(Engine(Path(tmp) / 'state.sqlite3'), 'agy')
            dispatch = {"kind": "CHAT", "task_id": "t", "id": "d", "role": "antigravity",
                        "attempt": 1, "payload": {}}
            prompt = runtime._prompt(dispatch)
            self.assertIn('read-only file tools', prompt)
            self.assertIn('normally denied here', prompt)
            self.assertIn('list_dir', prompt)
