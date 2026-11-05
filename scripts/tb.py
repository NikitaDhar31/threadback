"""Run ThreadBack from this checkout/plugin copy, whatever the current folder is."""

import sys

if sys.version_info < (3, 10):
    sys.exit(86)  # too old: scripts/tb tries the next Python

from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if len(sys.argv) == 3 and sys.argv[1] == "hook":
    # Hooks skip the command-line module: session end has 1.5 s in all, startup included.
    from threadback.hooks import run  # noqa: E402

    # Windows reads pipes in the local code page; Claude Code sends UTF-8 (folder names too)
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    run(sys.argv[2])
else:
    from threadback.cli import main  # noqa: E402

    main()
