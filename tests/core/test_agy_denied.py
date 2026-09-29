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
