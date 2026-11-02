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

from .redact import redact

# Under pythonw (login / desktop icon) every console child such as git would
# otherwise pop up its own flashing terminal window.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# A project folder may be someone else's repo, and git runs programs a repo's own config names:
# core.fsmonitor (status), filter drivers (status), log.showSignature -> gpg.program (log),
# and the same again inside submodules. ThreadBack only reads, so every git call goes through
# run_git(), which turns all of these off.
GIT_SAFE_CONFIG = ["-c", "core.fsmonitor=false", "-c", "log.showSignature=false",
                   "-c", "core.quotePath=false"]


def _git_exe() -> str | None:
    """git's full path, from absolute PATH entries only. Hooks run inside the project folder,
    and Windows looks in the current folder first: a repo could ship its own git.exe."""
    names = ("git.exe", "git.cmd", "git.bat") if os.name == "nt" else ("git",)
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry or not os.path.isabs(entry):
            continue  # "." or a relative entry would mean the project folder
        for name in names:
            path = os.path.join(entry, name)
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
    return None


def run_git(folder: str, *args: str, timeout: float = 3, ok: tuple[int, ...] = (0,)) -> str | None:
    """Run a read-only git command in `folder`; its stdout, or None if git failed (an exit code
    not in `ok`) or isn't there."""
    exe = _git_exe()
    if exe is None:
        return None
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    try:
        r = subprocess.run([exe, "-C", folder, *GIT_SAFE_CONFIG, *args], capture_output=True,
                           stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           creationflags=NO_WINDOW, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode in ok else None


def git_status(folder: str) -> list[str] | None:
    """`git status --porcelain` lines, or None when it isn't safe to run. Status can run a
    filter driver (filter.<name>.clean/process) on changed files, and no switch turns filters
    off. Filters from the user's own system or global config (Git LFS, often) are theirs; one
    set by the repo's own config (local, worktree, or a file it includes) means status is
    skipped. If git can't say where a filter comes from (older than 2.26), status is skipped."""
    filters = run_git(folder, "config", "--show-scope", "--get-regexp",
                      r"^filter\..*\.(clean|smudge|process)$", ok=(0, 1))  # 1: none set
    if filters is None:
        return None
    if any(line.split(None, 1)[0] in ("local", "worktree") for line in filters.splitlines() if line.strip()):
        return None
    out = run_git(folder, "status", "--porcelain", "--ignore-submodules=all")
    return None if out is None else [l for l in out.splitlines() if l.strip()]


def git_info(folder: str) -> dict | None:
    if not folder or not (Path(folder) / ".git").exists():
        return None
    log = (run_git(folder, "log", "-1", "--no-show-signature", "--format=%cI%x1f%s") or "").strip()
    branch = (run_git(folder, "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
    dirty = git_status(folder) or []
    # Branch names and commit messages can hold a pasted token too, and they reach Claude.
    info = {"branch": redact(branch), "uncommitted": len(dirty), "last_commit": None, "last_commit_msg": ""}
    if log:
        when, _, msg = log.partition("\x1f")
        info["last_commit"] = _to_local(when)
        info["last_commit_msg"] = redact(msg)
    return info


SCAN_FILES = 30


def claude_projects_root() -> Path:
    return Path(os.environ.get("THREADBACK_CLAUDE_DIR") or Path.home() / ".claude" / "projects")


def claude_dir_for(folder: str) -> Path:
    # Claude Code names the directory after the folder path with every
    # non-alphanumeric character replaced by "-": C:\Work\My App -> C--Work-My-App
    return claude_projects_root() / re.sub(r"[^A-Za-z0-9]", "-", str(folder))


_TIMESTAMP = re.compile(r'"timestamp"\s*:\s*"([^"]+)"')


def last_message_time(path: Path, tail: int = 65536) -> datetime:
    """When a chat really last had a message. The file's modified time is not enough:
    copying or syncing the folder resets it, making old chats look new."""
    mtime = datetime.fromtimestamp(path.stat().st_mtime).replace(microsecond=0)
    try:
        with path.open("rb") as fh:
            fh.seek(max(0, path.stat().st_size - tail))
            found = _TIMESTAMP.findall(fh.read().decode("utf-8", errors="replace"))
    except OSError:
        return mtime
    times = [t for t in (_to_local(x.replace("Z", "+00:00")) for x in found) if t]
    return max(times) if times else mtime


def claude_sessions(folder: str, limit: int = 3) -> list[dict]:
    """Most recent Claude Code sessions started in `folder`, newest first."""
    if not folder:
        return []
    d = claude_dir_for(folder)
    try:
        # Read the real last-message time of only the newest files by date, so a folder with
        # hundreds of chats doesn't slow down every session start.
        files = sorted(d.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)[:SCAN_FILES]
        dated = sorted(((last_message_time(f), f) for f in files), reverse=True)[:limit]
    except OSError:
        return []
    sessions = []
    for when, f in dated:
        s = {"id": f.stem, "title": "", "last_prompt": "", "last_active": when}
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
    # git writes UTC as "Z" (...T13:00:00Z), which Python 3.10's fromisoformat refuses.
    if iso.endswith(("Z", "z")):
        iso = iso[:-1] + "+00:00"
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


def last_work(ctx: dict) -> str:
    """One line on the most recent work seen in the folder, for projects with nothing saved."""
    git, chats = ctx.get("git"), ctx.get("claude") or []
    commit = (git["last_commit"], f"last commit: {git['last_commit_msg']}")         if git and git["last_commit"] else None
    chat = None
    if chats:
        title = chats[0]["title"] or chats[0]["last_prompt"][:70]
        if title:
            chat = (chats[0]["last_active"], f"last chat: {title}")
    newest = max((x for x in (commit, chat) if x), key=lambda x: x[0], default=None)
    return f"{newest[1]} ({ago(newest[0])})" if newest else ""


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
