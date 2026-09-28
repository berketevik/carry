"""The nightly harvest on Windows: one per-user Task Scheduler task, the counterpart of the launchd job.

Like the launchd plist, Carry keeps its own copy of the task definition and reads the schedule
back from it; `schtasks /Query` only answers whether the task is registered. The task runs
pythonw.exe (no console window at night) through `carry.nightly`, which moves the run under a
hidden console and appends its output to harvest.log as launchd's StandardOutPath does. Like
launchd, the task has no time limit.
"""
import os
from pathlib import Path
import subprocess
import sys
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from .background import system_tool
from .errors import CarryError

TASK_NAME = r'\Carry\Nightly harvest'
NS = {'t': 'http://schemas.microsoft.com/windows/2004/02/mit/task'}


def definition_path():
    base = os.environ.get('LOCALAPPDATA') or str(Path.home() / 'AppData' / 'Local')
    return Path(base) / 'Carry' / 'harvest-task.xml'


def interpreter():
    windowless = Path(sys.executable).with_name('pythonw.exe')
    return str(windowless) if windowless.is_file() else sys.executable


def task_xml(state_dir, hour, minute, extra=()):
    arguments = subprocess.list2cmdline(['-I', '-m', 'carry.nightly', '--workspace', str(state_dir), 'harvest', *extra])
    return f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="{NS['t']}">
  <RegistrationInfo><Description>Carry: drafts from the day's chats, for review in the app.</Description></RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-01-01T{int(hour):02d}:{int(minute):02d}:00</StartBoundary>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
  </Settings>
  <Actions Context="Author"><Exec><Command>{escape(interpreter())}</Command><Arguments>{escape(arguments)}</Arguments></Exec></Actions>
</Task>
'''


def _split(arguments):
    """Inverse of subprocess.list2cmdline, by the rules Windows programs parse their command line with."""
    args, current, pending, quoted, backslashes = [], [], False, False, 0
    for ch in arguments:
        if ch == '\\':
            backslashes += 1
            continue
        if ch == '"':
            current.append('\\' * (backslashes // 2))
            if backslashes % 2:
                current.append('"')
            else:
                quoted = not quoted
            backslashes, pending = 0, True
            continue
        current.append('\\' * backslashes)
        backslashes = 0
        if ch in ' \t' and not quoted:
            if pending or current:
                args.append(''.join(current))
            current, pending = [], False
        else:
            current.append(ch)
            pending = True
    current.append('\\' * backslashes)
    if pending or ''.join(current):
        args.append(''.join(current))
    return args


def registered():
    try:
        return subprocess.run([system_tool('schtasks.exe'), '/Query', '/TN', TASK_NAME], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def status():
    path = definition_path()
    try:
        root = ElementTree.fromstring(path.read_bytes())
        start = root.find('t:Triggers/t:CalendarTrigger/t:StartBoundary', NS).text
        args = _split(root.find('t:Actions/t:Exec/t:Arguments', NS).text or '')
    except (OSError, ElementTree.ParseError, AttributeError):
        return dict(installed=False, loaded=registered())
    hour, minute = (int(part) for part in start.split('T', 1)[1].split(':')[:2])
    opt = lambda flag: args[args.index(flag) + 1] if flag in args[:-1] else None
    return dict(installed=True, loaded=registered(), path=str(path), hour=hour, minute=minute,
                vault=opt('--vault'), language=opt('--language'), workspace=opt('--workspace'))


def _register(path):
    return subprocess.run([system_tool('schtasks.exe'), '/Create', '/TN', TASK_NAME, '/XML', str(path), '/F'],
                          capture_output=True).returncode == 0


def install(state_dir, hour, minute, extra=()):
    path = definition_path()
    previous = path.read_bytes() if path.is_file() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(task_xml(state_dir, hour, minute, extra).encode('utf-16'))
    if not _register(path):
        # Put the task that was there back rather than leave a definition Windows does not run.
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(previous)
            if not _register(path):
                raise CarryError('schedule_restore_failed')
        raise CarryError('schedule_load_failed')
    return status()


def remove():
    subprocess.run([system_tool('schtasks.exe'), '/Delete', '/TN', TASK_NAME, '/F'], capture_output=True)
    if registered():
        raise CarryError('schedule_remove_failed')
    definition_path().unlink(missing_ok=True)
    return dict(installed=False, loaded=False)
