"""Run ThreadBack from this checkout/plugin copy, whatever the current folder is."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from threadback.cli import main  # noqa: E402

main()
