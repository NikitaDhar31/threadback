"""Local project-context storage: one JSON file per project."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .redact import redact_obj

STATUSES = ("active", "paused", "blocked", "done")


_warned: set[str] = set()


def env_dir(name: str) -> Path | None:
    """A folder named by an environment variable, only if it's an absolute path. A project's
    .claude/settings.json can set variables for a Claude Code session, and a relative one
    would put ThreadBack's files (or reads) inside that project's folder."""
    value = os.environ.get(name)
    if not value:
        return None
    if not os.path.isabs(value):
        if name not in _warned:
            _warned.add(name)
            print(f"warning: ignoring {name}={value!r}: it must be an absolute path", file=sys.stderr)
        return None
    return Path(value)


def home() -> Path:
    """Data directory. Override with THREADBACK_HOME, an absolute path (tests use it)."""
    return env_dir("THREADBACK_HOME") or Path.home() / ".threadback"


def home_inside(folder: str) -> bool:
    """True if the data directory is `folder` or inside it, e.g. THREADBACK_HOME set by a
    project's own settings to a folder in the project: nothing may be written there."""
    data, top = _norm(str(home())), _norm(folder)
    return data == top or data.startswith(top + os.sep)


def projects_dir() -> Path:
    d = home() / "projects"
    if not d.is_dir():
        # Only the user can read what ThreadBack keeps (macOS/Linux; Windows ignores modes).
        home().mkdir(mode=0o700, parents=True, exist_ok=True)
        d.mkdir(mode=0o700, exist_ok=True)
    _make_private()
    return d


_private_checked = False


def _make_private() -> None:
    """Versions before 0.7.3 created the data folders and files with the default modes, often
    readable by other users on a shared Mac or Linux machine. Once per process, take group and
    other access away from what's already there. Windows: profile folders are private already."""
    global _private_checked
    if _private_checked or os.name == "nt":
        return
    _private_checked = True
    root = home()
    paths = [root, root / "projects"]
    try:
        paths += [f for f in root.iterdir() if f.is_file() and not f.is_symlink()]
        paths += [f for f in (root / "projects").iterdir() if f.is_file() and not f.is_symlink()]
    except OSError:
        pass
    for path in paths:
        try:
            mode = path.stat().st_mode
            if mode & 0o077:
                path.chmod(mode & 0o700)
        except OSError:
            pass  # not ours to change (a shared folder set up on purpose): leave it


def now() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        raise ValueError(f"Cannot make a file name from project name {name!r}")
    return slug


def new_project(name: str, folder: str | None = None) -> dict:
    ts = now()
    return {
        "name": name,
        "slug": slugify(name),
        "status": "active",
        "created": ts,
        "updated": ts,
        "folder": folder or "",
        "next_action": "",
        "last_state": "",
        "links": [],  # [{"label": "Claude: auth design", "url": "https://..."}]
        "decisions": [],  # [{"date", "text", "why"}]
        "tasks": [],  # [{"text", "done", "added", "done_on"}]
        "blockers": [],  # [{"text", "added"}]
        "ideas": [],  # [{"text", "added"}]
        "sessions": [],  # [{"date", "summary", "next"}]
        "claude_sessions": [],  # recorded by the plugin hooks: [{"id", "started", "ended"}]
    }


def path_for(slug: str) -> Path:
    return projects_dir() / f"{slug}.json"


_held = 0  # lock() is re-entrant within one process


@contextmanager
def lock(timeout: float = 5.0):
    """One writer at a time across processes: the hooks, the panel's buttons and saves can
    run together, and each must read the latest file before changing it. Writers take this
    around their whole read-change-save. A lock left by a crashed process goes stale after
    30 s; past `timeout` the caller goes ahead rather than hang a session."""
    global _held
    if _held:
        _held += 1
        try:
            yield
        finally:
            _held -= 1
        return
    path = home() / ".lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    fd = None
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except (FileExistsError, PermissionError):
            # Held by another writer (Windows says PermissionError while it's being deleted).
            # A lock older than 30 s was left by a crashed process: remove it and try again.
            try:
                if time.time() - path.stat().st_mtime > 30:
                    path.unlink()
            except OSError:
                pass  # released meanwhile, or not ours to remove (say, a folder)
        except OSError:
            pass  # can't create it here at all; the deadline below decides
        # Every path through the loop checks the deadline, so nothing can spin for ever.
        if time.monotonic() > deadline:
            print(f"threadback: couldn't lock {path} within {timeout:g}s; going ahead without it",
                  file=sys.stderr)
            break
        time.sleep(0.03)
    _held += 1
    try:
        yield
    finally:
        _held -= 1
        if fd is not None:
            os.close(fd)
            try:
                path.unlink()
            except OSError:
                pass


def save(project: dict) -> Path:
    project.update(redact_obj(project))  # never write a pasted key to disk
    project["updated"] = now()
    p = path_for(project["slug"])
    _write_private(p, json.dumps(project, indent=2, ensure_ascii=False))
    return p


def _write_private(p: Path, text: str) -> None:
    # A temp file of its own, so two writers never share one; replace() swaps it in whole.
    tmp = p.parent / f".{p.stem}.{os.getpid()}.{time.monotonic_ns()}.tmp"
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)  # private to the user
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        for attempt in range(20):
            try:
                os.replace(tmp, p)
                break
            except PermissionError:  # Windows: a reader has the file open for a moment
                if attempt == 19:
                    raise
                time.sleep(0.05)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# Which folder each Claude Code session started in, from the SessionStart hook's payload.
# `tb here` uses it instead of the shell's current folder, which Claude can change.
SESSION_ROOTS_KEEP = 200


def _session_roots_path() -> Path:
    projects_dir()  # creates the data folder (and keeps it private)
    return home() / "session-roots.json"


def _session_roots() -> dict:
    try:
        data = json.loads(_session_roots_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def record_session_root(session_id: str, folder: str) -> None:
    """Caller holds lock()."""
    roots = {k: v for k, v in _session_roots().items() if k != session_id}
    roots[session_id] = {"folder": os.path.realpath(folder), "date": now()}
    keep = sorted(roots.items(), key=lambda kv: kv[1].get("date", ""))[-SESSION_ROOTS_KEEP:]
    _write_private(_session_roots_path(), json.dumps(dict(keep), indent=1, ensure_ascii=False))


def session_root(session_id: str) -> str | None:
    entry = _session_roots().get(session_id)
    folder = entry.get("folder") if isinstance(entry, dict) else None
    return folder if isinstance(folder, str) and folder else None


def reload(project: dict) -> dict:
    """The project as it is on disk now; take it inside lock() before changing it."""
    return next(p for p in load_all() if p["slug"] == project["slug"])


def add_session(project: dict, summary: str = "", next_action: str = "") -> dict:
    """Log a work session and update the next action. Caller saves the project."""
    s = {"date": now(), "summary": summary.strip(), "next": next_action.strip()}
    if s["next"]:
        project["next_action"] = s["next"]
    if s["summary"] or s["next"]:
        project["sessions"].append(s)
        project["auto_saves"] = []  # a real save covers the chats auto-captured before it
    return s


def load_all() -> list[dict]:
    projects = []
    for p in sorted(projects_dir().glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            # stderr: a hook's stdout must hold its one JSON answer and nothing else
            print(f"warning: skipping unreadable {p.name}: {e}", file=sys.stderr)
            continue
        base = new_project(data.get("name") or p.stem)
        base.update(data)  # tolerate files written by older versions or by hand
        base["slug"] = p.stem
        projects.append(base)
    return projects


def _norm(path: str) -> str:
    # realpath: one folder can be spelled two ways, through a link (/var is /private/var on
    # macOS) or a Windows short name (RUNNER~1), and both must match the saved folder.
    return os.path.normcase(os.path.realpath(path)).rstrip("\\/")


def find_by_folder(path: str) -> dict | None:
    """The project whose folder is `path` or contains it (deepest match wins)."""
    target = _norm(path)
    best = None
    for p in load_all():
        if not p["folder"]:
            continue
        folder = _norm(p["folder"])
        if (target == folder or target.startswith(folder + os.sep)) and \
                (best is None or len(folder) > len(_norm(best["folder"]))):
            best = p
    return best


NAME_CHARS = 60


def display_name(name: str) -> str:
    """A project name as it may reach Claude: one short line of plain name characters.
    Names often come from folder names, which anyone shipping a repo or archive chooses, and on
    macOS/Linux can even hold line breaks; this keeps them from reading like instructions."""
    name = re.sub(r"[\x00-\x1f\x7f-\x9f  ]+", " ", name)  # line breaks, control chars
    name = re.sub(r"[^\w .,()&+#-]+", " ", name)  # letters (any script), digits, light punctuation
    name = " ".join(name.split())[:NAME_CHARS].strip()
    return name or "(unnamed)"


def has_control_chars(text: str) -> bool:
    """Line breaks or control characters: a path like that is not shown as a command."""
    return bool(re.search(r"[\x00-\x1f\x7f-\x9f  ]", text))


def resume_command(folder: str, session_id: str | None = None) -> str:
    """The line that reopens a project's latest chat, meant to be pasted: a folder name can
    hold quotes or $( ), so it is quoted for the shell people use on this system."""
    import shlex

    claude = "claude" + (f" --resume {shlex.quote(session_id)}" if session_id else "")
    if os.name != "nt":
        return f"cd {shlex.quote(folder)} && {claude}"
    # Windows PowerShell 5.1 has no `&&` and cmd has no single quotes; `cd '...'; claude`
    # runs in PowerShell and Git Bash. PowerShell also reads curly quotes as quotes, and
    # [ ] as wildcards, so such folders get its own literal form.
    if re.search(r"['‘’‚‛\[\]]", folder):
        literal = re.sub(r"(['‘’‚‛])", r"\1\1", folder)
        return f"Set-Location -LiteralPath '{literal}'; {claude}"
    return f"cd '{folder}'; {claude}"


def register_folder(path: str) -> dict:
    """Create a project for a folder, named after it. Refuses drive roots and home."""
    folder = Path(path).resolve()
    if folder == Path.home().resolve() or folder.parent == folder:
        raise ValueError(f"Won't register {folder} as a project; open Claude Code in a project folder.")
    base = display_name(folder.name)
    name, n = base, 2
    while path_for(slugify(name)).exists():
        name, n = f"{base} {n}", n + 1
    p = new_project(name, folder=str(folder))
    save(p)
    return p


def record_claude_session(project: dict, session_id: str, ended: bool = False, keep: int = 30) -> None:
    """Remember a Claude Code session (from the official hook input). Caller saves."""
    sessions = project.setdefault("claude_sessions", [])
    entry = next((s for s in sessions if s["id"] == session_id), None)
    if entry is None:
        entry = {"id": session_id, "started": now(), "ended": ""}
        sessions.append(entry)
    else:  # resumed: move to the end so the newest stays last
        sessions.remove(entry)
        sessions.append(entry)
    entry["last_seen"] = now()
    if ended:
        entry["ended"] = entry["last_seen"]
    del sessions[:-keep]


def find(query: str) -> dict:
    """Find a project by slug, exact name, or unique name prefix/substring."""
    projects = load_all()
    q = query.lower()
    for test in (
        lambda p: p["slug"] == q or p["name"].lower() == q,
        lambda p: p["slug"].startswith(slugify(query)) or p["name"].lower().startswith(q),
        lambda p: q in p["name"].lower(),
    ):
        hits = [p for p in projects if test(p)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            names = ", ".join(p["name"] for p in hits)
            raise LookupError(f"'{query}' matches several projects: {names}")
    raise LookupError(f"No project matches '{query}'. Create it with: threadback new \"{query}\"")
