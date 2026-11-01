"""Local project-context storage: one JSON file per project."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

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


def save(project: dict) -> Path:
    project["updated"] = now()
    p = path_for(project["slug"])
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(project, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
    return p


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
            print(f"warning: skipping unreadable {p.name}: {e}")
            continue
        base = new_project(data.get("name") or p.stem)
        base.update(data)  # tolerate files written by older versions or by hand
        base["slug"] = p.stem
        projects.append(base)
    return projects


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path)).rstrip("\\/")


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
