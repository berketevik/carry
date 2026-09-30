"""Finding and starting other programs the same way on every platform.

detached: a process that outlives the one that started it. macOS/Linux: a new session, as before.
Windows: a new process group with a hidden console (so the child's own subprocesses open no
window), outside the caller's job object when the job allows that: a client may close a hook's
job, and every process in it, once the hook returns.

which: shutil.which, except that on Windows the current folder is never searched. Windows looks
there before PATH, and a hook runs inside the vault: a planted claude.cmd would run instead.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys


def detached(args, **options):
    if sys.platform != 'win32':
        return subprocess.Popen(args, start_new_session=True, **options)
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    try:
        return subprocess.Popen(args, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **options)
    except PermissionError:  # the job forbids breakaway: stay in it rather than not start at all
        return subprocess.Popen(args, creationflags=flags, **options)


def which(name):
    if sys.platform != 'win32':
        return shutil.which(name)
    extensions = [''] if Path(name).suffix else os.environ.get('PATHEXT', '.COM;.EXE;.BAT;.CMD').split(';')
    for folder in os.environ.get('PATH', '').split(os.pathsep):
        if not folder or not Path(folder).is_absolute():
            continue
        for extension in extensions:
            candidate = Path(folder) / (name + extension)
            if candidate.is_file():
                return str(candidate)
    return None


def system_tool(name):
    """A Windows tool by its full path, so a same-named program elsewhere is never run."""
    return str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / name)
