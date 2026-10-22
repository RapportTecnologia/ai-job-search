#!/usr/bin/env python3
"""Initialize ignored working copies from versioned setup templates, without overwriting."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
DOCUMENT_FOLDERS = ('cv', 'linkedin', 'diplomas', 'references', 'projects', 'applications', 'postings')

PRIVATE = ('.claude/skills/job-application-assistant/',
           '.claude/skills/job-scraper/search-queries.md', 'CLAUDE.md',
           'cv/main_example.tex', 'documents/', '.local-backups/',
           '.agenticspace/', 'automation/config.json', 'automation/state/',
           'job_scraper/portals.json')


def private_path(name):
    return name != 'documents/.gitkeep' and any(
        name.startswith(p) if p.endswith('/') else name == p for p in PRIVATE)


def check_private(root):
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
    leaked = [name for name in tracked if name and private_path(name)]
    if leaked:
        raise ValueError('Private paths remain tracked/staged: ' + ', '.join(leaked))


def initialize(root):
    source = root / 'templates/setup'
    if not source.is_dir():
        raise ValueError('Missing templates/setup')
    created, preserved = [], []
    sources = sorted(p for p in source.rglob('*') if p.is_file())
    if not sources:
        raise ValueError('No setup templates found')
    # Validate all destinations before writing anything.
    for template in sources:
        relative = template.relative_to(source)
        dest = root / relative
        if template.is_symlink() or any(p.is_symlink() for p in [dest, *dest.parents] if p != root.parent):
            raise ValueError('Refusing symlink in template/destination: ' + str(relative))
        if not dest.resolve().is_relative_to(root.resolve()):
            raise ValueError('Destination escapes workspace')
        if dest.exists() and not dest.is_file():
            raise ValueError('Destination is not a file: ' + str(relative))
        ignored = subprocess.run(['git', 'check-ignore', '--no-index', '-q', '--', str(relative)], cwd=root)
        if ignored.returncode != 0:
            raise ValueError('Destination must be gitignored: ' + str(relative))
    for template in sources:
        relative = template.relative_to(source)
        dest = root / relative
        if dest.exists():
            preserved.append(str(relative))
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with dest.open('xb') as output:
                output.write(template.read_bytes())
        except FileExistsError:
            preserved.append(str(relative))
        else:
            created.append(str(relative))
    for name in DOCUMENT_FOLDERS:
        (root / 'documents' / name).mkdir(parents=True, exist_ok=True)
    return {'created': created, 'preserved': preserved}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Verify no private paths are tracked; do not initialize')
    args = parser.parse_args()
    try:
        check_private(ROOT)
        if args.check:
            print('Private workspace guard: OK')
        else:
            print(json.dumps(initialize(ROOT), indent=2))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f'setup_workspace: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
