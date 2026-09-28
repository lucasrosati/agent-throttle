"""throttle-logrotate: keep only the last N days of the agent-throttle logs ([logs] retention_days, default 60).

Logs: semaphore.log, guard-blocks.log and load.log in [logs] dir. Every line starts with a date (YYYY-MM-DD); the TSV
header stays, and a line without a recognizable date stays (never delete what is not understood). The rewrite is
atomic (temporary file in the same folder + os.replace) and only happens if the file did not change size while it was
read: if solo or the hook appends a line in the middle, it tries again (up to 5 times) instead of losing the line.
"""
import argparse
import datetime as dt
import os
import re
import sys
import tempfile
import time

from . import config

DATE_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})')


def keep(line, cutoff):
    m = DATE_RE.match(line)
    if not m:
        return True  # header, empty line or unknown format
    try:
        return dt.date.fromisoformat(m.group(1)) >= cutoff
    except ValueError:
        return True


def rotate(path, cutoff, dry_run):
    for _ in range(5):
        try:
            before = os.stat(path)
        except FileNotFoundError:
            return f'{path}: does not exist'
        with open(path, encoding='utf-8', errors='replace') as fh:
            lines = fh.readlines()
        kept = [ln for ln in lines if keep(ln, cutoff)]
        dropped = len(lines) - len(kept)
        if not dropped:
            return f'{path}: {len(lines)} lines, nothing before {cutoff}'
        if dry_run:
            return f'{path}: would remove {dropped} of {len(lines)} lines (before {cutoff})'
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix='.rotate-')
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            out.writelines(kept)
        os.chmod(tmp, before.st_mode & 0o777)
        if os.stat(path).st_size == before.st_size:
            os.replace(tmp, path)
            return f'{path}: removed {dropped} of {len(lines)} lines (before {cutoff})'
        os.unlink(tmp)
        time.sleep(1)
    return f'{path}: file kept changing during the rotation, tried 5 times; trying again next run'


def main(argv=None):
    cfg = config.load()
    ap = argparse.ArgumentParser(prog='throttle-logrotate', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--days', type=int, default=cfg['logs']['retention_days'])
    ap.add_argument('--dir', default=cfg['logs']['dir'], help='folder with the logs (default: [logs] dir)')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--today', help='YYYY-MM-DD (tests)')
    a = ap.parse_args(argv)
    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    cutoff = today - dt.timedelta(days=a.days)
    stamp = dt.datetime.now().strftime('%F %T')
    for name in config.LOG_FILES.values():
        print(f'[throttle-logrotate {stamp}] {rotate(os.path.join(a.dir, name), cutoff, a.dry_run)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
