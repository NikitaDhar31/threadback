"""Read-only signals gathered automatically from the local machine.

Nothing here talks to the network or needs credentials:
- git: last commit and uncommitted changes in the project folder.
- Claude Code: session transcripts that Claude Code itself keeps under
  ~/.claude/projects/<encoded-folder>/<session-id>.jsonl. This is an
  undocumented local format, so every field is optional and failures are
  silently ignored rather than breaking the dashboard.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

# Under pythonw (login / desktop icon) every console child such as git would
# otherwise pop up its own flashing terminal window.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def git_info(folder: str) -> dict | None:
    if not folder or not (Path(folder) / ".git").exists():
        return None

    def git(*args: str) -> str:
        r = subprocess.run(
            ["git", "-C", folder, *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            creationflags=NO_WINDOW,
        )
        return r.stdout.strip() if r.returncode == 0 else ""

    try:
        log = git("log", "-1", "--format=%cI%x1f%s")
        branch = git("rev-parse", "--abbrev-ref", "HEAD")
        dirty = [l for l in git("status", "--porcelain").splitlines() if l.strip()]
    except (OSError, subprocess.SubprocessError):
        return None
    info = {"branch": branch, "uncommitted": len(dirty), "last_commit": None, "last_commit_msg": ""}
    if log:
        when, _, msg = log.partition("\x1f")
        info["last_commit"] = _to_local(when)
        info["last_commit_msg"] = msg
    return info


def claude_projects_root() -> Path:
    return Path(os.environ.get("THREADBACK_CLAUDE_DIR") or Path.home() / ".claude" / "projects")


def claude_dir_for(folder: str) -> Path:
    # Claude Code names the directory after the folder path with every
    # non-alphanumeric character replaced by "-": C:\Work\My App -> C--Work-My-App
    return claude_projects_root() / re.sub(r"[^A-Za-z0-9]", "-", str(folder))


def claude_sessions(folder: str, limit: int = 3) -> list[dict]:
    """Most recent Claude Code sessions started in `folder`, newest first."""
    if not folder:
        return []
    d = claude_dir_for(folder)
    try:
        files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    except OSError:
        return []
    sessions = []
    for f in files:
        s = {"id": f.stem, "title": "", "last_prompt": "",
             "last_active": datetime.fromtimestamp(f.stat().st_mtime).replace(microsecond=0)}
        try:
            with f.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    # Cheap pre-filter: transcripts are mostly large message records.
                    if '-title"' not in line and '"lastPrompt"' not in line and '"summary"' not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    t = rec.get("type")
                    if t == "custom-title" and rec.get("customTitle"):
                        s["title"] = rec["customTitle"]
                    elif t == "ai-title" and rec.get("aiTitle") and not s["title"]:
                        s["title"] = rec["aiTitle"]
                    elif t == "summary" and rec.get("summary") and not s["title"]:
                        s["title"] = rec["summary"]
                    elif t == "last-prompt" and rec.get("lastPrompt"):
                        s["last_prompt"] = rec["lastPrompt"]
        except OSError:
            continue
        sessions.append(s)
    return sessions


def _to_local(iso: str) -> datetime | None:
    try:
        return datetime.fromisoformat(iso).astimezone().replace(tzinfo=None, microsecond=0)
    except ValueError:
        return None


def parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def merged_claude_sessions(project: dict, limit: int = 3) -> list[dict]:
    """Sessions recorded by the plugin hooks (official, stable), enriched with
    titles from the transcript scan when that still works. If a Claude Code
    update breaks the scan, the recorded sessions keep working."""
    by_id: dict[str, dict] = {}
    for r in project.get("claude_sessions", []):
        seen = parse_ts(r.get("last_seen") or r.get("ended") or r.get("started"))
        if r.get("id") and seen:
            by_id[r["id"]] = {"id": r["id"], "title": "", "last_prompt": "", "last_active": seen}
    try:
        scanned = claude_sessions(project.get("folder", ""), limit=max(limit, 5))
    except Exception:  # undocumented format: never let it break the dashboard
        scanned = []
    for s in scanned:
        if s["id"] in by_id:
            rec = by_id[s["id"]]
            rec["title"], rec["last_prompt"] = s["title"], s["last_prompt"]
            rec["last_active"] = max(rec["last_active"], s["last_active"])
        else:
            by_id[s["id"]] = s
    return sorted(by_id.values(), key=lambda s: s["last_active"], reverse=True)[:limit]


def context(project: dict) -> dict:
    """Everything known about a project, plus a single 'last_activity' time."""
    git = git_info(project.get("folder", ""))
    claude = merged_claude_sessions(project)
    # Only real work counts: editing the project file (new, link, ...) is not activity.
    candidates = [parse_ts(s.get("date")) for s in project.get("sessions", [])]
    if git and git["last_commit"]:
        candidates.append(git["last_commit"])
    if claude:
        candidates.append(claude[0]["last_active"])
    candidates = [c for c in candidates if c]
    return {"git": git, "claude": claude, "last_activity": max(candidates) if candidates else None}


def suggest_resume(projects_with_ctx: list[tuple[dict, dict]]) -> tuple[dict, dict] | None:
    """Pick the project to resume: most recently touched one that isn't done or paused."""
    pool = [pc for pc in projects_with_ctx if pc[0].get("status") in ("active", "blocked")]
    pool = [pc for pc in pool if pc[1]["last_activity"]]
    if not pool:
        return None
    # Prefer active over blocked when both exist; blocked work usually can't be resumed.
    active = [pc for pc in pool if pc[0]["status"] == "active"] or pool
    return max(active, key=lambda pc: pc[1]["last_activity"])


def ago(dt: datetime | None) -> str:
    if not dt:
        return "never"
    secs = (datetime.now() - dt).total_seconds()
    if secs < 90:
        return "just now"
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if secs >= size:
            n = int(secs // size)
            return f"{n} {unit}{'s' if n != 1 else ''} ago"
    return "just now"
