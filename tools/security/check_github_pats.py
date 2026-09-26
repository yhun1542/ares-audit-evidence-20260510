#!/usr/bin/env python3
"""Reject GitHub token-shaped plaintext in tracked files without printing tokens."""
import argparse
import pathlib
import re
import subprocess
import sys

PAT = re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{20,255}|github_pat_[A-Za-z0-9_]{20,255})")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--staged', action='store_true', help='Scan the complete Git index (pre-commit use).')
    parser.add_argument('--selftest', action='store_true')
    args = parser.parse_args()
    if args.selftest:
        assert PAT.search(b'gh' + b'p_' + b'A' * 36)
        assert PAT.search(b'github_' + b'pat_' + b'B' * 82)
        assert not PAT.search(b'REDACTED_GITHUB_PAT')
        print('GitHub token guard selftest PASS')
        return 0
    root = pathlib.Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel']).decode().strip())
    listing = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-s', '-z'])
    bad = []
    cache = {}
    for entry in listing.split(b'\0'):
        if not entry:
            continue
        metadata, raw_path = entry.split(b'\t', 1)
        mode, oid, stage = metadata.split()
        if stage != b'0' or mode == b'160000':
            continue
        path = raw_path.decode(errors='surrogateescape')
        if args.staged:
            if oid not in cache:
                data = subprocess.check_output(['git', '-C', str(root), 'cat-file', 'blob', oid.decode()])
                cache[oid] = len(PAT.findall(data))
            count = cache[oid]
        else:
            file = root / path
            if not file.is_file() or file.is_symlink():
                continue
            count = len(PAT.findall(file.read_bytes()))
        if count:
            bad.append((path, count))
    for path, count in bad:
        print(f'ERROR: GitHub token-shaped plaintext: {path} ({count} occurrence(s)); value suppressed.', file=sys.stderr)
    print(f'GitHub token guard: {len(bad)} file(s), {sum(n for _, n in bad)} occurrence(s).')
    return 1 if bad else 0

if __name__ == '__main__':
    sys.exit(main())
