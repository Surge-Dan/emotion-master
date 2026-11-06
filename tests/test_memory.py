"""Exercise real CLI boundaries using synthetic records in a disposable private root."""
import concurrent.futures
import json
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'memory.py'


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='emotion-master-test-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'private'
        self.payload = self.base / 'input.json'
        self.case = {
            'relationship': 'married, synthetic', 'goal': 'share care work',
            'summary': 'User is planning a discussion', 'source': 'synthetic turn 1',
            'action': {'id': 'care-01', 'text': 'Discuss a schedule',
                       'status': 'selected', 'source': 'synthetic turn 1'},
        }

    def cli(self, *args, success=True, root=None):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), '--root', str(root or self.root), *args],
            capture_output=True, text=True, encoding='utf-8',
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        return json.loads(result.stderr)

    def grant(self):
        self.cli('grant', '--confirm-consent')

    def save(self, revision=0, success=True, case_id='r1', correction=False):
        self.payload.write_text(json.dumps(self.case), encoding='utf-8')
        args = ['save', '--case-id', case_id, '--input', str(self.payload),
                '--expected-revision', str(revision)]
        if correction:
            args.append('--correction')
        return self.cli(*args, success=success)

    def test_no_files_before_consent(self):
        self.assertEqual(self.cli('status')['consent'], 'not_granted')
        self.cli('grant', success=False)
        self.save(success=False)
        self.cli('recall', '--case-id', 'r1', success=False)
        self.assertFalse(self.root.exists())
        self.root.mkdir()
        self.cli('status')
        self.cli('show')
        self.assertEqual(list(self.root.iterdir()), [])

    def test_save_recall_idempotency_and_stale_write(self):
        self.grant()
        self.assertEqual(self.save()['revision'], 1)
        self.assertTrue(self.save()['unchanged'])
        self.case['summary'] = 'Changed after a real conversation'
        self.save(success=False)
        self.assertEqual(self.save(1)['revision'], 2)
        result = self.cli('recall', '--case-id', 'r1')
        self.assertEqual(result['current'], self.case)
        self.assertEqual(len(result['events']), 2)

    def test_pause_resume_revoke_and_management(self):
        self.grant()
        self.save()
        self.cli('pause')
        self.save(1, success=False)
        self.cli('recall', '--case-id', 'r1', success=False)
        self.assertIn('r1', self.cli('show')['cases'])
        self.cli('resume', success=False)
        self.cli('resume', '--confirm-consent')
        self.cli('revoke', success=False)
        self.cli('revoke', '--confirm')
        self.assertEqual(self.cli('show')['cases'], {})
        self.save(success=False)
        self.grant()
        self.assertEqual(self.cli('show')['cases'], {})

    def test_action_evidence_and_correction(self):
        self.grant()
        self.case['action']['status'] = 'agreed'
        self.save(success=False)
        self.case['action']['partner_source'] = 'Both explicitly accepted in synthetic turn 2'
        self.save()
        self.case['action']['status'] = 'completed'
        self.save(1, success=False)
        self.case['action']['feedback'] = 'User reports the care shift was actually completed'
        self.save(1)
        self.case['action']['status'] = 'selected'
        self.save(2, success=False)
        self.save(2, correction=True)
        result = self.cli('recall', '--case-id', 'r1')
        self.assertTrue(result['events'][-1]['correction'])

    def test_paths_and_compact_schema(self):
        self.grant()
        self.save(case_id='../escape', success=False)
        self.case['summary'] = 'x' * 1201
        self.save(success=False)
        self.case['summary'] = 'ok'
        self.case['raw_chat'] = 'unapproved raw data'
        self.save(success=False)
        self.cli('grant', '--confirm-consent', root=SCRIPT.parent, success=False)
        git_dir = self.base / 'other-repo'
        (git_dir / '.git').mkdir(parents=True)
        self.cli('grant', '--confirm-consent', root=git_dir / 'private', success=False)

    def test_corruption_does_not_reset_and_explicit_purge(self):
        self.grant()
        state = self.root / 'emotion-master.json'
        state.write_text('{broken', encoding='utf-8')
        self.cli('grant', '--confirm-consent', success=False)
        self.save(success=False)
        self.assertEqual(state.read_text(encoding='utf-8'), '{broken')
        interrupted = self.root / '.emotion-master-writing-interrupted.tmp'
        interrupted.write_text('partial synthetic data', encoding='utf-8')
        self.cli('purge', '--confirm')
        self.assertFalse(state.exists())
        self.assertFalse(interrupted.exists())
        self.assertEqual(self.cli('status')['consent'], 'not_granted')

    def test_export_and_delete(self):
        self.grant()
        self.save()
        export = self.base / 'export.json'
        self.cli('export', '--destination', str(export))
        exported = json.loads(export.read_text(encoding='utf-8'))
        self.assertEqual(exported['cases']['r1']['current'], self.case)
        self.cli('export', '--destination', str(export), success=False)
        self.cli('delete-case', '--case-id', 'r1', '--confirm')
        self.assertEqual(self.cli('show')['cases'], {})
        self.assertTrue(export.exists())  # External copies remain explicitly user-managed.

    def test_concurrent_updates_reject_stale_revision(self):
        self.grant()
        self.save()
        paths = []
        for i in range(4):
            case = dict(self.case, summary='Concurrent version ' + str(i))
            path = self.base / ('payload-' + str(i) + '.json')
            path.write_text(json.dumps(case), encoding='utf-8')
            paths.append(path)

        def execute(path):
            return subprocess.run(
                [sys.executable, str(SCRIPT), '--root', str(self.root), 'save',
                 '--case-id', 'r1', '--input', str(path), '--expected-revision', '1'],
                capture_output=True,
            ).returncode

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            codes = list(pool.map(execute, paths))
        self.assertEqual(codes.count(0), 1)
        result = self.cli('recall', '--case-id', 'r1')
        self.assertEqual(result['revision'], 2)
        self.assertEqual(len(result['events']), 2)

    def test_history_retention(self):
        self.grant()
        for revision in range(53):
            self.case['summary'] = 'Synthetic update ' + str(revision)
            self.save(revision)
        result = self.cli('recall', '--case-id', 'r1')
        self.assertEqual(result['revision'], 53)
        self.assertEqual(len(result['events']), 50)
        self.assertEqual(result['events'][0]['revision'], 4)

    def test_atomic_replace_failure_preserves_prior_record(self):
        self.grant()
        self.save()
        state_path = self.root / 'emotion-master.json'
        original = state_path.read_bytes()
        spec = importlib.util.spec_from_file_location('emotion_memory', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        replacement = json.loads(original)
        replacement['consent'] = 'paused'
        with mock.patch.object(module.os, 'replace', side_effect=OSError('synthetic interruption')):
            with self.assertRaises(OSError):
                module.atomic_write(state_path, replacement)
        self.assertEqual(state_path.read_bytes(), original)
        self.assertEqual(list(self.root.glob('.emotion-master-writing-*.tmp')), [])

    def test_case_limit_requires_user_choice(self):
        self.grant()
        for index in range(30):
            self.save(case_id='r' + str(index))
        self.save(case_id='r30', success=False)
        self.assertEqual(self.cli('status')['case_count'], 30)


if __name__ == '__main__':
    unittest.main()
