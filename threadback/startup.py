"""Show the welcome window at Windows login, plus a desktop icon for any time.

- Login: a script in the user's Startup folder.
- Desktop: a "ThreadBack" shortcut (.lnk) that opens the same window.
Both are per-user, need no admin rights or registry changes, and
`threadback startup remove` deletes both.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .sources import NO_WINDOW

SCRIPT_NAME = "ThreadBack.cmd"
SHORTCUT_NAME = "ThreadBack.lnk"


def startup_dir() -> Path:
    override = os.environ.get("THREADBACK_STARTUP_DIR")
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise OSError("APPDATA is not set; startup install is only supported on Windows.")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def desktop_dir() -> Path | None:
    override = os.environ.get("THREADBACK_DESKTOP_DIR")
    if override:
        return Path(override)
    try:  # handles a Desktop redirected to OneDrive
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
            capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None


def script_path() -> Path:
    return startup_dir() / SCRIPT_NAME


def _runner() -> Path:
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")  # no console window
    return pythonw if pythonw.exists() else exe


def _package_root() -> Path:
    return Path(__file__).resolve().parent.parent


def script_text() -> str:
    lines = ["@echo off", "rem Created by ThreadBack - shows the welcome-back window at login."]
    if os.environ.get("THREADBACK_HOME"):
        lines.append(f'set "THREADBACK_HOME={os.environ["THREADBACK_HOME"]}"')
    lines += [f'cd /d "{_package_root()}"', f'start "" "{_runner()}" -m threadback welcome', ""]
    return "\r\n".join(lines)


def create_shortcut(folder: Path) -> Path:
    lnk = folder / SHORTCUT_NAME
    q = lambda s: str(s).replace("'", "''")  # PowerShell single-quote escaping
    ps = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
        "$s.TargetPath = '{target}'; $s.Arguments = '-m threadback welcome';"
        "$s.WorkingDirectory = '{cwd}'; $s.Description = 'ThreadBack - what was I working on?';"
        "$s.Save()"
    ).format(lnk=q(lnk), target=q(_runner()), cwd=q(_package_root()))
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   check=True, capture_output=True, timeout=30, creationflags=NO_WINDOW)
    return lnk


def install(desktop: bool = True) -> list[Path]:
    p = script_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(script_text(), encoding="utf-8", newline="")  # already \r\n; don't double it
    made = [p]
    d = desktop_dir() if desktop else None
    if d:
        made.append(create_shortcut(d))
    return made


def remove() -> bool:
    removed = False
    targets = [script_path()]
    d = desktop_dir()
    if d:
        targets.append(d / SHORTCUT_NAME)
    for t in targets:
        if t.exists():
            t.unlink()
            removed = True
    return removed
