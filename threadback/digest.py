"""Short, free summary of what happened in a project folder (no AI, no network).

Old Claude Code transcripts are megabytes of tool output; this pulls out only
what helps someone remember: commit messages, the top of the README, and per
chat its title, the user's own messages and Claude's last answer. The result is
small enough for Claude to read cheaply when drafting a catch-up save.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

from .sources import NO_WINDOW, _to_local, ago, claude_dir_for, claude_sessions

MAX_CHARS = 6000


def _clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 3] + "..."


def _git(folder: str, *args: str) -> str:
    if not folder or not (Path(folder) / ".git").exists():
        return ""
    try:
        r = subprocess.run(["git", "-C", folder, "-c", "core.quotePath=false", *args], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=10, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout if r.returncode == 0 else ""


def uncommitted(folder: str) -> list[str]:
    """Changed and new files not yet committed: often the work that was in progress."""
    return [l[3:].strip() for l in _git(folder, "status", "--porcelain").splitlines() if l.strip()]


def git_log(folder: str, since: datetime | None = None, limit: int = 10) -> list[dict]:
    """Recent commits, newest first: date, subject and changed files."""
    args = ["log", f"-{limit}", "--format=%x1e%cI%x1f%s", "--name-only"]
    if since:
        args.append(f"--since={since.isoformat()}")
    commits = []
    for block in _git(folder, *args).split("\x1e")[1:]:
        head, *files = [l for l in block.splitlines() if l.strip()]
        when, _, msg = head.partition("\x1f")
        commits.append({"date": _to_local(when), "msg": msg, "files": files})
    return commits


def readme(folder: str, lines: int = 20, chars: int = 1200) -> str:
    for name in ("README.md", "README.txt", "README", "readme.md"):
        f = Path(folder) / name
        if f.is_file():
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return ""
            kept = [l.rstrip() for l in text.splitlines() if l.strip()][:lines]
            return "\n".join(kept)[:chars]
    return ""


def _user_text(rec: dict) -> str:
    """The user's own words in a transcript record; '' for tool results, commands, reminders."""
    if rec.get("isMeta") or rec.get("isSidechain") or rec.get("isCompactSummary"):
        return ""
    content = rec.get("message", {}).get("content")
    parts = [content] if isinstance(content, str) else [
        c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text"]
    # Slash commands, hook output and system reminders all arrive wrapped in <tags>.
    return " ".join(p.strip() for p in parts if p.strip() and not p.lstrip().startswith("<"))


def _assistant_text(rec: dict) -> str:
    if rec.get("isSidechain"):
        return ""
    content = rec.get("message", {}).get("content")
    if not isinstance(content, list):
        return ""
    return " ".join(c.get("text", "") for c in content
                    if isinstance(c, dict) and c.get("type") == "text").strip()


def chat(path: Path) -> dict:
    """User messages and the last answer from one transcript (undocumented format: best effort)."""
    asked, last_answer = [], ""
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            # Cheap pre-filter: skip progress, snapshot and other bulky records.
            if '"user"' not in line and '"assistant"' not in line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") == "user":
                text = _user_text(rec)
                if text:
                    asked.append(text)
            elif rec.get("type") == "assistant":
                last_answer = _assistant_text(rec) or last_answer
    return {"asked": asked, "last_answer": last_answer}


def chats(folder: str, since: datetime | None = None, limit: int = 4) -> list[dict]:
    """Recent Claude Code chats in `folder`, oldest first, skipping ones with no user words."""
    out = []
    for s in claude_sessions(folder, limit=limit):
        if since and s["last_active"] <= since:
            continue
        try:
            c = chat(claude_dir_for(folder) / f"{s['id']}.jsonl")
        except OSError:
            continue
        if c["asked"]:
            out.append({**s, **c})
    return list(reversed(out))


def build(folder: str, since: datetime | None = None, max_chars: int = MAX_CHARS,
          n_chats: int = 4, messages: int = 6) -> str:
    """Plain-text digest. With `since`, only activity after it (an incremental catch-up)."""
    out = [f"Digest of {folder}" + (f" since {since:%Y-%m-%d %H:%M}" if since else "")]
    if not since:
        top = readme(folder)
        if top:
            out += ["", "README (top):", top]
    commits = git_log(folder, since)
    if commits:
        out += ["", "Commits (newest first):"]
        for c in commits:
            more = f" +{len(c['files']) - 3} more" if len(c["files"]) > 3 else ""
            files = _clip(", ".join(c["files"][:3]), 100) + more
            day = f"{c['date']:%Y-%m-%d}" if c["date"] else "?"
            out.append(f"- {day} {c['msg']}" + (f"  [{files}]" if files else ""))
    dirty = uncommitted(folder)
    if dirty:
        more = f" +{len(dirty) - 6} more" if len(dirty) > 6 else ""
        out += ["", f"Uncommitted changes ({len(dirty)}): " + _clip(", ".join(dirty[:6]), 300) + more]
    for c in chats(folder, since, n_chats):
        out += ["", f"Claude chat: {c['title'] or '(untitled)'} - {c['last_active']:%Y-%m-%d} "
                    f"({ago(c['last_active'])})", "  User asked:"]
        asked = c["asked"]
        if len(asked) > messages:  # the start says what it was about; the end, where it stopped
            keep = messages // 3
            shown = asked[:keep] + [f"... ({len(asked) - messages} more messages) ..."] + asked[keep - messages:]
        else:
            shown = asked
        out += [f"  - {_clip(m, 220)}" for m in shown]
        if c["last_answer"]:
            out.append(f"  Claude's last answer: {_clip(c['last_answer'], 500)}")
    if len(out) == 1:
        out.append("No commits or Claude Code chats found" + (" since then." if since else "."))
    text = "\n".join(out)
    return text if len(text) <= max_chars else text[: max_chars - 20] + "\n[...truncated]"


def last_saved(project: dict) -> datetime | None:
    """When context was last saved; a catch-up only needs activity after this."""
    dates = [s.get("date") for s in project.get("sessions", []) if s.get("date")]
    try:
        return datetime.fromisoformat(max(dates)) if dates else None
    except ValueError:
        return None
