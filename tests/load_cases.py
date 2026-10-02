#!/usr/bin/env python3
"""Load verdict and portable signal readers, with fake signals and processes."""
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(os.environ.get('THROTTLE_ROOT', Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(ROOT / 'lib'))
from agent_throttle import config, load, machine  # noqa: E402

n = 0


def ok(name, cond):
    global n
    assert cond, name
    n += 1
    print('ok  ' + name)


cfg = config.load(Path('/nonexistent-config-file'))
cfg['load']['max_agents'] = 5
safe = dict(cores=6, ram=8 * 2**30, free_pct=50, pressure='green', swap=0, thermal=[], processes=[])


def verdict(**changes):
    return load.verdict(dict(safe, **changes), cfg, 0)


ok('safe live signals pass', verdict()[0] == [])
ok('swap delta over 512 MB in 30 min blocks', load.verdict(safe, cfg, 513)[0])
ok('swap delta at 512 MB passes', not load.verdict(safe, cfg, 512)[0])
ok('yellow pressure with rising swap blocks', load.verdict(dict(safe, pressure='yellow'), cfg, 1)[0])
ok('yellow without rising swap passes', not load.verdict(dict(safe, pressure='yellow'), cfg, 0)[0])
ok('red pressure blocks', verdict(pressure='red')[0])
ok('thermal warning blocks', verdict(thermal=['thermal warning'])[0])
ok('total swap over RAM/8 only warns', not verdict(swap=1100)[0] and verdict(swap=1100)[1])
ok('failed read means do not launch', all(verdict(**{key: None})[0] for key in safe))
ok('free below 25 blocks but equality passes', verdict(free_pct=24)[0] and not verdict(free_pct=25)[0])
ok('no recent swap history blocks', load.verdict(safe, cfg, None)[0])
processes = [dict(pid=i, parent=0, name='codex', args=f'codex {cmd}', rss=100)
             for i, cmd in enumerate(('app-server', 'exec-server', 'mcp-server', 'exec'), start=1)]
ok('helper processes are not counted as agents', load.count_agents(processes, cfg['load']) == (1, 1))
ok('agents at ceiling block', verdict(processes=processes[-1:] * 5)[0])
ok('agents below ceiling pass', not verdict(processes=processes[-1:] * 4)[0])
custom = dict(cfg['load'], agent_processes=['assistant'], helper_processes=['helper'])
ok('agent and helper names are configurable', load.count_agents([dict(pid=1, parent=0, name='assistant', args='assistant helper')], custom) == (0, 0))
with patch.object(machine, 'cores', return_value=11), patch.object(machine, 'ram_bytes', return_value=32 * 2**30), \
        patch.object(machine, 'memory', return_value=(60, 10000)), patch.object(machine, 'swap_mb', return_value=0), \
        patch.object(machine, 'pressure', return_value='green'), patch.object(machine, 'thermal', return_value=[]), \
        patch.object(machine, 'processes', return_value=[]):
    snap = load.snapshot(cfg)
    ok('verdict uses live cores and RAM, no constants', snap['cores'] == 11 and snap['ram'] == 32 * 2**30)
with patch.object(machine.platform, 'system', return_value='Linux'), patch.object(machine, 'read_text') as reader:
    reader.return_value = 'some avg10=0.00 avg60=0 avg300=0 total=0\nfull avg10=0.00 avg60=0 avg300=0 total=0\n'
    ok('Linux PSI green', machine.pressure(1, 10) == 'green')
    reader.return_value = 'some avg10=2.00 avg60=0 avg300=0 total=0\nfull avg10=0.00 avg60=0 avg300=0 total=0\n'
    ok('Linux PSI yellow', machine.pressure(1, 10) == 'yellow')
    reader.return_value = 'some avg10=20.00 avg60=0 avg300=0 total=0\nfull avg10=10.00 avg60=0 avg300=0 total=0\n'
    ok('Linux PSI red', machine.pressure(1, 10) == 'red')
    reader.return_value = 'invalid'
    ok('invalid PSI stays unknown', machine.pressure(1, 10) is None)
    reader.return_value = 'MemTotal: 8388608 kB\nSwapTotal: 2048 kB\nSwapFree: 1024 kB\n'
    ok('Linux swap reader', machine.swap_mb() == 1)
with patch.object(machine.platform, 'system', return_value='Darwin'), patch.object(machine, 'command') as command:
    command.return_value = 'total = 1024.00M used = 128.50M free = 895.50M'
    ok('macOS swap reader', machine.swap_mb() == 128.5)
    command.return_value = 'Note: No thermal warning level has been recorded\nNote: No performance warning level has been recorded\n'
    ok('macOS no thermal warning', machine.thermal() == [])
    command.return_value = 'CPU_Speed_Limit = 80\n'
    ok('macOS thermal warning', bool(machine.thermal()))
    command.return_value = None
    ok('thermal read failure remains unknown', machine.thermal() is None)
with tempfile.TemporaryDirectory(prefix='thermal-') as t:
    zone = Path(t, 'thermal_zone0')
    zone.mkdir()
    (zone / 'temp').write_text('40000')
    (zone / 'trip_point_0_type').write_text('critical')
    (zone / 'trip_point_0_temp').write_text('90000')
    real_path = Path
    with patch.object(machine.platform, 'system', return_value='Linux'), patch.object(machine, 'Path', side_effect=
            lambda path: real_path(t) if str(path) == '/sys/class/thermal' else real_path(path)):
        ok('Linux thermal trips read live', machine.thermal() == [])
        (zone / 'temp').write_text('90000')
        ok('Linux thermal trip blocks', bool(machine.thermal()))
        (zone / 'trip_point_0_type').unlink()
        ok('Linux without usable thermal trips stays unknown', machine.thermal() is None)
with patch.object(machine, 'command') as command:
    command.side_effect = ['1 0 1024 /usr/bin/codex\n', '1 codex exec\n']
    ok('process readers preserve executable and arguments', machine.processes()[0]['args'] == 'codex exec')
    command.side_effect = ['bad', 'bad']
    ok('malformed processes stay unknown', machine.processes() is None)
legacy = dict(cfg, _max_swap_explicit=True)
ok('explicit legacy absolute swap limit still blocks', load.verdict(dict(safe, swap=4000), legacy, 0)[0])
with tempfile.TemporaryDirectory(prefix='load-cases-') as t:
    log = Path(t, 'load.log')
    now = dt.datetime(2026, 1, 1, 12)
    log.write_text('2026-01-01 11:00:00\t50\tgreen\t0\t0\t0\tno\n'
                   '2026-01-01 11:35:00\t50\tgreen\t100\t0\t0\tno\n'
                   '2026-01-01 11:55:00\t50\tgreen\t600\t0\t0\tno\n')
    ok('swap uses first sample in 30-minute window', load.window_delta(log, 613, 30, now) == 513)
    ok('future samples do not supply baseline', load.window_delta(log, 613, 30, now - dt.timedelta(hours=2)) is None)
    cfg['logs']['dir'] = t
    load.write_sample(safe, cfg, now)
    ok('load log appends seven columns preserving total sessions', len(log.read_text().splitlines()[-1].split('\t')) == 7)
print(f'{n}/{n} cases')
