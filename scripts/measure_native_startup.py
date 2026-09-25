"""Time a fresh native process to view appearance and workspace response.

Requires a GUI session and an explicit synthetic workspace. OS caches stay warm;
this is not a reboot/cold-machine benchmark. Only this script's children stop.
"""
import argparse
import json
from pathlib import Path
import selectors
import subprocess
import time

from measure_packaging import distribution


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('app', type=Path)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--samples', type=int, default=10)
    args = parser.parse_args()
    visible, ready = [], []
    for _ in range(args.samples):
        start = time.perf_counter()
        proc = subprocess.Popen([str(args.app.resolve() / 'Contents/MacOS/Carry')],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=dict(PATH='/usr/bin:/bin', CARRY_DESKTOP_WORKSPACE=args.workspace, CARRY_MEASURE_STARTUP='1'))
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ)
        seen = {}
        try:
            while 'carry_workspace_ready' not in seen and time.perf_counter() - start < 30:
                if not selector.select(timeout=1):
                    continue
                line = proc.stdout.readline().decode().strip()
                if not line:
                    raise RuntimeError('app exited before workspace response')
                seen[line] = time.perf_counter() - start
            if not all(k in seen for k in ('carry_ui_ready', 'carry_workspace_ready')):
                raise RuntimeError('native startup timed out')
            visible.append(seen['carry_ui_ready'])
            ready.append(seen['carry_workspace_ready'])
        finally:
            selector.close()
            proc.terminate()
            proc.wait(timeout=10)
    report = dict(environment='same development Mac, new process, warm OS caches',
                  native_view_appeared=distribution(visible), workspace_response=distribution(ready))
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
