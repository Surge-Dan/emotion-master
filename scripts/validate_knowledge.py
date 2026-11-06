"""Check local navigation, source IDs, coverage and source-record completeness."""
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote


def main():
    root = Path(__file__).resolve().parents[1]
    errors = []
    records = json.loads((root / 'references/sources/catalog.json').read_text(encoding='utf-8'))
    sources = {record['id'] for record in records}
    if len(sources) != len(records):
        errors.append('Duplicate source IDs')
    for record in records:
        for key in ('id', 'title', 'url', 'source_type', 'use_status', 'review_depth',
                    'locator', 'limitations', 'checked_at', 'conflict_of_interest'):
            if not record.get(key):
                errors.append(record['id'] + ' missing ' + key)
    documents = list(root.rglob('*.md'))
    for path in documents:
        content = path.read_text(encoding='utf-8')
        for link in re.findall(r'\[[^\]]*\]\(([^)]+)\)', content):
            if re.match(r'[a-zA-Z]+://', link) or link.startswith('#'):
                continue
            target = (path.parent / unquote(link.split('#')[0])).resolve()
            if not target.exists():
                errors.append(str(path.relative_to(root)) + ' broken link ' + link)
        if path.parent.name == 'knowledge':
            for sid in re.findall(r'\b(?:[PRBFCNSE]\d\d)\b', content):
                if sid not in sources:
                    errors.append(str(path.relative_to(root)) + ' unknown source ' + sid)
    index = (root / 'references/knowledge/index.md').read_text(encoding='utf-8')
    cards = [p for p in (root / 'references/knowledge').glob('*.md') if p.stem != 'index']
    for path in cards:
        if path.name not in index:
            errors.append('Unreachable card ' + path.name)
        content = path.read_text(encoding='utf-8')
        for section in ('何时使用', '本技能操作步骤', '交付与反馈', '适用限制', '可回查依据'):
            if '## ' + section not in content:
                errors.append(path.name + ' missing ' + section)
    print(json.dumps({'documents': len(documents), 'knowledge_cards': len(cards),
                      'sources': len(records), 'errors': errors}, ensure_ascii=False, indent=2))
    return bool(errors)


if __name__ == '__main__':
    sys.exit(main())
