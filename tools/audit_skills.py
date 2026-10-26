#!/usr/bin/env python3
"""Read-only inventory for Hermes-style SKILL.md files. Never modifies scanned files."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path, help='Directory containing skill folders or SKILL.md files')
    parser.add_argument('--max-bytes', type=int, default=15_000, help='Flag files larger than this size (default: 15000)')
    parser.add_argument('--json', action='store_true', help='Emit machine-readable JSON')
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    if not root.is_dir():
        parser.error(f'not a directory: {root}')

    paths = sorted(p for p in root.rglob('SKILL.md') if p.is_file())
    hashes: dict[str, list[str]] = defaultdict(list)
    rows = []
    for path in paths:
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        hashes[digest].append(str(path))
        text = data.decode('utf-8', errors='replace')
        first_nonempty = next((line.strip() for line in text.splitlines() if line.strip()), '')
        rows.append({
            'path': str(path),
            'bytes': len(data),
            'sha256': digest,
            'first_nonempty_line': first_nonempty[:160],
            'over_size_limit': len(data) > args.max_bytes,
            'empty': not text.strip(),
        })

    duplicates = [items for items in hashes.values() if len(items) > 1]
    report = {
        'root': str(root),
        'skill_count': len(rows),
        'max_bytes': args.max_bytes,
        'oversize_count': sum(row['over_size_limit'] for row in rows),
        'empty_count': sum(row['empty'] for row in rows),
        'duplicate_groups': duplicates,
        'skills': rows,
        'mutations_performed': False,
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Root: {root}")
        print(f"Skills found: {len(rows)} | oversized: {report['oversize_count']} | empty: {report['empty_count']}")
        for row in rows:
            flags = []
            if row['over_size_limit']:
                flags.append('OVER_LIMIT')
            if row['empty']:
                flags.append('EMPTY')
            suffix = f" [{' '.join(flags)}]" if flags else ''
            print(f"{row['bytes']:>7} B  {row['sha256'][:12]}  {row['path']}{suffix}")
        for index, group in enumerate(duplicates, start=1):
            print(f"\nExact duplicate group {index}:")
            for item in group:
                print(f"  {item}")
        print('\nRead-only audit complete; no files were changed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())