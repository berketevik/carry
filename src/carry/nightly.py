"""Entry point of the Windows nightly task: `carry ... harvest` with its output appended to
harvest.log, since Task Scheduler (unlike launchd's StandardOutPath) keeps none.

The task starts pythonw, which has no console, so no window opens at night. But every console
program pythonw starts (claude, git) would get a visible window of its own: the run therefore
restarts once under python.exe with a hidden console, which those programs share.
"""
import subprocess
import sys
import traceback
from pathlib import Path

from .background import detached
from .cli import main


def run(argv):
    windowless = Path(sys.executable)
    if sys.platform == 'win32' and windowless.name.lower() == 'pythonw.exe':
        child = detached([str(windowless.with_name('python.exe')), '-I', '-m', 'carry.nightly', *argv],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return child.wait()
    state_dir = argv[argv.index('--workspace') + 1]
    streams = sys.stdout, sys.stderr
    with open(Path(state_dir) / 'harvest.log', 'a', encoding='utf-8') as log:
        sys.stdout = sys.stderr = log
        try:
            return main(argv)
        except Exception:
            traceback.print_exc(file=log)  # nowhere else to see why the night's run died
            raise
        finally:
            sys.stdout, sys.stderr = streams


if __name__ == '__main__':
    sys.exit(run(sys.argv[1:]))
