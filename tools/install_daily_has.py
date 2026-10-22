#!/usr/bin/env python3
"""Render/install a daily systemd user timer for /scrape-to-has (Linux)."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def units(hour):
    def escaped(value):
        return str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
    path = escaped(os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin'))
    service = f'''[Unit]
Description=AI Job Search /scrape-to-has
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory={escaped(ROOT)}
Environment="PATH={path}"
ExecStart="{escaped(sys.executable)}" "{escaped(ROOT / 'tools/scrape_to_has.py')}" run
UMask=0077
TimeoutStartSec=6h
'''
    timer = f'''[Unit]
Description=Daily job search and Hub Agentic Space publication

[Timer]
OnCalendar=*-*-* {hour}:00 America/Fortaleza
Persistent=true
Unit=ai-job-search-has.service

[Install]
WantedBy=timers.target
'''
    return service, timer


def main():
    import re
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--time', default='09:00', help='HH:MM in America/Fortaleza')
    parser.add_argument('--output', type=Path, help='Render only, without installing')
    args = parser.parse_args()
    if not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', args.time):
        parser.error('time must be HH:MM')
    folder = args.output or Path.home() / '.config/systemd/user'
    folder.mkdir(parents=True, exist_ok=True)
    for suffix, content in zip(('service', 'timer'), units(args.time)):
        path = folder / ('ai-job-search-has.' + suffix)
        if path.exists() and path.read_text() != content:
            shutil.copy2(path, path.with_suffix(path.suffix + '.bak'))
        path.write_text(content)
    if not args.output:
        subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
        subprocess.run(['systemctl', '--user', 'enable', '--now', 'ai-job-search-has.timer'], check=True)
        subprocess.run(['systemctl', '--user', 'list-timers', 'ai-job-search-has.timer', '--no-pager'], check=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
