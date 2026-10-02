"""Coordinated append of short TSV events. Existing headers and legacy rows are left intact."""
import fcntl
import sys
from pathlib import Path

SEMAPHORE_HEADER = 'date\twait_s\tduration_s\trc\tcwd\tcommand\tnote\tslot\tw\tw_adjust\torigin\tpeak_mb\twait_reason'


def append(path, header, row):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0, 2)
        line = '\t'.join('' if v is None else str(v).replace('\t', ' ').replace('\n', ' ').replace('\r', ' ') for v in row)
        fh.write((header + '\n' if fh.tell() == 0 else '') + line + '\n')
        fh.flush()


if __name__ == '__main__':
    append(sys.argv[1], SEMAPHORE_HEADER, sys.argv[2:])
