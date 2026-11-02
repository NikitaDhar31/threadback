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


def home() -> Path:
    """Data directory. Override with THREADBACK_HOME (used by tests)."""
    return Path(os.environ.get("THREADBACK_HOME") or Path.home() / ".threadback")


def projects_dir() -> Path:
    d = home() / "projects"
    d.mkdir(parents=True, exist_ok=True)
    return d


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
    # A temp file of its own, so two writers never share one; replace() swaps it in whole.
    tmp = p.parent / f".{project['slug']}.{os.getpid()}.{time.monotonic_ns()}.tmp"
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(project, indent=2, ensure_ascii=False))
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
    return p


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


def register_folder(path: str) -> dict:
    """Create a project for a folder, named after it. Refuses drive roots and home."""
    folder = Path(path).resolve()
    if folder == Path.home().resolve() or folder.parent == folder:
        raise ValueError(f"Won't register {folder} as a project; open Claude Code in a project folder.")
    name, n = folder.name, 2
    while path_for(slugify(name)).exists():
        name, n = f"{folder.name} {n}", n + 1
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
