"""Silent, fail-open command hook. Receipts live locally, never in model context."""
import argparse
import sys

from .capture import CLIENTS, ingest
from .config import open_workspace


def main(argv=None):
    parser = argparse.ArgumentParser(prog='carry-hook')
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--client', required=True, choices=CLIENTS)
    args = parser.parse_args(argv)
    try:
        ingest(open_workspace(args.workspace), args.client, sys.stdin.buffer)
    except Exception:
        # No exception text: a path, payload, or credential may be in the message.
        print('carry-hook: capture unavailable; check workspace permissions and capture status.', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
