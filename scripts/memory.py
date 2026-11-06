"""Consent-gated compact local records. Standard library only, Python 3.9+."""
import argparse
import contextlib
import copy
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from datetime import datetime, timezone


VERSION = 1
MAX_CASES = 30
MAX_EVENTS = 50
STATES = ['proposed', 'selected', 'agreed', 'attempted', 'completed', 'declined']
CASE_FIELDS = {
    'relationship', 'goal', 'summary', 'needs', 'partner_position',
    'constraints', 'recommendation', 'tradeoffs', 'unknowns', 'next_check',
    'source', 'action',
}
ACTION_FIELDS = {'id', 'text', 'status', 'source', 'partner_source', 'feedback'}


def now():
    return datetime.now(timezone.utc).isoformat()


def safe_path(value):
    path = Path(value).expanduser().resolve()
    repo = Path(__file__).resolve().parents[1]
    if path == repo or repo in path.parents:
        raise ValueError('Personal records must be outside the skill directory')
    for parent in (path, *path.parents):
        if (parent / '.git').exists():
            raise ValueError('Personal records must be outside Git working trees')
    return path


def valid_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', value):
        raise ValueError('Use a non-identifying ID of 1-64 letters, digits, _ or -')
    return value


def short_text(value, name, required=False):
    if not isinstance(value, str) or len(value) > 1200:
        raise ValueError(name + ' must be text of at most 1200 characters')
    if required and not value.strip():
        raise ValueError(name + ' must be nonempty')


def validate_case(case):
    if not isinstance(case, dict) or set(case) - CASE_FIELDS:
        raise ValueError('Unknown case fields')
    for field in ('relationship', 'goal', 'summary', 'source'):
        short_text(case.get(field), field, required=True)
    for field, value in case.items():
        if field != 'action':
            short_text(value, field)
    if 'action' in case:
        action = case['action']
        if not isinstance(action, dict) or set(action) - ACTION_FIELDS:
            raise ValueError('Unknown action fields')
        valid_id(action.get('id'))
        for field in ('text', 'source'):
            short_text(action.get(field), field, required=True)
        if action.get('status') not in STATES:
            raise ValueError('Invalid action status')
        for field, value in action.items():
            short_text(value, field)
        if action['status'] == 'agreed':
            short_text(action.get('partner_source'), 'partner_source', required=True)
        if action['status'] in ('attempted', 'completed'):
            short_text(action.get('feedback'), 'feedback', required=True)


def validate_store(state):
    if not isinstance(state, dict) or set(state) != {'version', 'consent', 'cases'}:
        raise ValueError('Invalid store schema')
    if type(state['version']) is not int or state['version'] != VERSION:
        raise ValueError('Unsupported version')
    if state['consent'] not in ('active', 'paused', 'revoked'):
        raise ValueError('Unsupported version or consent state')
    cases = state['cases']
    if not isinstance(cases, dict) or len(cases) > MAX_CASES:
        raise ValueError('Invalid case collection')
    if state['consent'] == 'revoked' and cases:
        raise ValueError('Revoked records must contain no cases')
    for case_id, record in cases.items():
        valid_id(case_id)
        if not isinstance(record, dict) or set(record) != {'revision', 'current', 'events'}:
            raise ValueError('Invalid case record')
        if type(record['revision']) is not int or record['revision'] < 1:
            raise ValueError('Invalid revision')
        validate_case(record['current'])
        events = record['events']
        if not isinstance(events, list) or not 1 <= len(events) <= MAX_EVENTS:
            raise ValueError('Invalid event history')
        prior_revision = 0
        for event in events:
            if not isinstance(event, dict) or set(event) != {'at', 'revision', 'correction', 'case'}:
                raise ValueError('Invalid event')
            if type(event['revision']) is not int or type(event['correction']) is not bool:
                raise ValueError('Invalid event metadata')
            if event['revision'] <= prior_revision:
                raise ValueError('Event revisions must increase')
            prior_revision = event['revision']
            datetime.fromisoformat(event['at'])
            validate_case(event['case'])
        if events[-1]['case'] != record['current'] or events[-1]['revision'] != record['revision']:
            raise ValueError('Current case and history disagree')


@contextlib.contextmanager
def locked(root):
    # The file is empty of personal content and remains after purge to avoid lock races.
    with (root / '.emotion-master.lock').open('a+b') as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def read_store(path):
    if not path.exists():
        return None
    if path.stat().st_size > 128_000_000:
        raise ValueError('Store exceeds supported size')
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
        validate_store(state)
        return state
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError('Invalid store. No changes made. Use an explicit purge to delete it.') from exc


def atomic_write(path, state):
    validate_store(state)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                         dir=path.parent, prefix='.emotion-master-writing-',
                                         suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def run(args):
    root = safe_path(args.root)
    command = args.command
    consent_commands = ('grant', 'resume')
    if command in consent_commands and not args.confirm_consent:
        raise ValueError('Explicit informed user consent is required')
    if command in ('delete-case', 'purge', 'revoke') and not args.confirm:
        raise ValueError('Explicit user authorization for deletion or revocation is required')
    if not root.exists():
        if command == 'grant':
            root.mkdir(parents=True, exist_ok=True)
        elif command in ('status', 'show', 'recall', 'purge'):
            if command == 'recall':
                raise ValueError('Active consent is required')
            return {'consent': 'not_granted', 'cases': {}}
        else:
            raise ValueError('Grant consent before saving records')
    if not root.is_dir():
        raise ValueError('Root must be a dedicated directory')
    state_path = root / 'emotion-master.json'
    if state_path.is_symlink():
        raise ValueError('Store file cannot be a symbolic link')
    if not state_path.exists() and command in ('status', 'show', 'recall'):
        if command == 'recall':
            raise ValueError('Active consent is required')
        return {'consent': 'not_granted', 'cases': {}}
    with locked(root):
        # Explicit purge can remove a corrupt file, without resetting it during ordinary operations.
        if command == 'purge':
            if state_path.exists():
                state_path.unlink()
            for temporary in root.glob('.emotion-master-writing-*.tmp'):
                if temporary.is_file() or temporary.is_symlink():
                    temporary.unlink()
            return {'purged': True}
        state = read_store(state_path)
        if command == 'grant':
            if state and state['consent'] == 'paused':
                raise ValueError('Use resume for paused records')
            if state is None or state['consent'] == 'revoked':
                state = {'version': VERSION, 'consent': 'active', 'cases': {}}
        elif command == 'status':
            return {'consent': state['consent'] if state else 'not_granted',
                    'case_count': len(state['cases']) if state else 0}
        elif command in ('show', 'export'):
            result = state or {'version': VERSION, 'consent': 'not_granted', 'cases': {}}
            if command == 'show':
                return result
            destination = safe_path(args.destination)
            if destination == state_path:
                raise ValueError('Export destination must differ from the store')
            # Exclusive creation prevents overwriting either records or unrelated user files.
            descriptor = os.open(str(destination), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                json.dump(result, handle, ensure_ascii=False, indent=2)
            return {'exported': str(destination)}
        else:
            if state is None:
                raise ValueError('No consent record')
            if command == 'pause':
                if state['consent'] != 'active':
                    raise ValueError('Only active consent can be paused')
                state['consent'] = 'paused'
            elif command == 'resume':
                if state['consent'] != 'paused':
                    raise ValueError('Only paused records can be resumed')
                state['consent'] = 'active'
            elif command == 'revoke':
                state = {'version': VERSION, 'consent': 'revoked', 'cases': {}}
            elif command == 'delete-case':
                state['cases'].pop(valid_id(args.case_id), None)
            else:
                if state['consent'] != 'active':
                    raise ValueError('Active consent is required for recall and save')
                if command == 'recall':
                    case_id = valid_id(args.case_id)
                    return state['cases'].get(case_id, {'found': False})
                if command == 'save':
                    case_id = valid_id(args.case_id)
                    payload_path = Path(args.input)
                    if payload_path.stat().st_size > 40_000:
                        raise ValueError('Input exceeds compact record limit')
                    case = json.loads(payload_path.read_text(encoding='utf-8-sig'))
                    validate_case(case)
                    existing = state['cases'].get(case_id)
                    if existing and existing['current'] == case:
                        return {'saved': False, 'unchanged': True, 'revision': existing['revision']}
                    revision = existing['revision'] if existing else 0
                    if args.expected_revision != revision:
                        raise ValueError('Revision conflict. Recall current record before updating')
                    if not existing and len(state['cases']) >= MAX_CASES:
                        raise ValueError('Case limit reached. User must select records to delete')
                    old_action = existing['current'].get('action') if existing else None
                    new_action = case.get('action')
                    if old_action and new_action and old_action['id'] == new_action['id']:
                        old_rank = STATES.index(old_action['status'])
                        new_rank = STATES.index(new_action['status'])
                        if new_rank < old_rank and not args.correction:
                            raise ValueError('Backward action updates require explicit correction')
                    events = copy.deepcopy(existing['events']) if existing else []
                    events.append({'at': now(), 'revision': revision + 1,
                                   'correction': args.correction, 'case': case})
                    state['cases'][case_id] = {
                        'revision': revision + 1, 'current': case,
                        'events': events[-MAX_EVENTS:],
                    }
        atomic_write(state_path, state)
        result = {'consent': state['consent'], 'updated': True}
        if command == 'save':
            result['revision'] = state['cases'][args.case_id]['revision']
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, help='Dedicated personal directory outside Git')
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('grant', 'resume'):
        child = sub.add_parser(command)
        child.add_argument('--confirm-consent', action='store_true')
    for command in ('pause', 'status', 'show'):
        sub.add_parser(command)
    for command in ('purge', 'revoke'):
        child = sub.add_parser(command)
        child.add_argument('--confirm', action='store_true')
    child = sub.add_parser('delete-case')
    child.add_argument('--case-id', required=True)
    child.add_argument('--confirm', action='store_true')
    child = sub.add_parser('recall')
    child.add_argument('--case-id', required=True)
    child = sub.add_parser('export')
    child.add_argument('--destination', required=True)
    child = sub.add_parser('save')
    child.add_argument('--case-id', required=True)
    child.add_argument('--input', required=True)
    child.add_argument('--expected-revision', type=int, required=True)
    child.add_argument('--correction', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(run(args), ensure_ascii=False))
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
