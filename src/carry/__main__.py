"""`python -m carry` runs the same CLI as the `carry` command."""
import sys

from .cli import main

sys.exit(main())
