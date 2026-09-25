"""Local atomic publication and one workspace writer lock (macOS/Linux)."""
import fcntl
import os
import tempfile
import time
from contextlib import contextmanager

from .errors import CarryError


@contextmanager
def writer_lock(workspace, timeout=3.0):
    workspace.validate()
    workspace.state_dir.mkdir(parents=True, exist_ok=True)
    with open(workspace.state_dir / 'records.lock', 'a') as handle:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise CarryError('writer_busy_retry')
                time.sleep(0.02)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.carry-write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
