"""Non-blocking exclusive file locks: flock on macOS/Linux, a one-byte msvcrt lock on Windows.

Both are released when the process exits. On Windows the lock belongs to the process that
took it, so it cannot be handed to a child through an inherited handle.
"""
import sys

if sys.platform == 'win32':
    import msvcrt

    def try_lock(handle):
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def unlock(handle):
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def try_lock(handle):
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def unlock(handle):
        fcntl.flock(handle, fcntl.LOCK_UN)
