"""Claude Code plugin hooks (official, documented interface).

Claude Code runs `threadback hook <event>` and passes JSON on stdin with
`session_id` and `cwd`. On session start we record the session and hand Claude
the project's saved context; on session end we record when it finished.

Session end gets a 1.5 s budget in all (plugin hooks can't raise it), so it only notes the
transcript; capture_pending() reads it the next time the project is opened or shown.

A hook must never break a Claude session: every error is logged to
~/.threadback/hook-errors.log and the hook still exits 0.
"""

from __future__ import annotations

import json
import os
import sys

from pathlib import Path

from . import store
from .redact import fence, redact

# digest, sources (git, chat files) and traceback load only where used: session end
# has 1.5 s in all, Python's own start included.

MAX_CONTEXT_CHARS = 6000
TASK_CHARS = 160
MAX_CAPTURE_TRIES = 3


def run(event: str) -> None:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        with store.lock(timeout=1.0 if event == "session-end" else 5.0):
            out = session_start(payload) if event == "session-start" else session_end(payload)
        if out:
            print(json.dumps(out, ensure_ascii=False))
    except Exception:
        import traceback

        log_error(event, traceback.format_exc())


def log_error(what: str, detail: str) -> None:
    """Hooks never fail a session: what went wrong goes to ~/.threadback/hook-errors.log."""
    try:
        log = store.home() / "hook-errors.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(f"--- {store.now()} {what}\n{detail.rstrip()}\n")
    except OSError:
        pass


def _project_for(payload: dict) -> dict | None:
    return store.find_by_folder(payload.get("cwd") or os.getcwd())


def note_tb_path() -> None:
    """Tell the optional panel plugin where this plugin's tb.py is: installed plugins
    live in a different folder on every machine and after every update."""
    tb = str(Path(__file__).resolve().parent.parent / "scripts" / "tb.py")
    f = store.home() / "tb-path"
    try:
        if not f.exists() or f.read_text(encoding="utf-8") != tb:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(tb, encoding="utf-8")
    except OSError:
        pass  # only the panel's buttons need it


def session_start(payload: dict) -> dict:
    note_tb_path()
    p = _project_for(payload)
    if p is None:
        text = ("ThreadBack: this folder is not a tracked project. If the user wants to track it, "
                "/threadback-save registers it and can catch up from the folder's history.")
    else:
        if payload.get("session_id"):
            store.record_claude_session(p, payload["session_id"])
        if capture_pending(p) or payload.get("session_id"):
            store.save(p)
        from .sources import context

        text = brief_for_claude(p, context(p))
        if not p["sessions"] and not p["next_action"]:
            text += catch_up_hint(p)
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}


def session_end(payload: dict) -> None:
    p = _project_for(payload)
    if p is not None and payload.get("session_id"):
        store.record_claude_session(p, payload["session_id"], ended=True)
        pending = [c for c in p.get("pending_captures", []) if c["session_id"] != payload["session_id"]]
        pending.append({"session_id": payload["session_id"], "cwd": payload.get("cwd") or "",
                        "transcript_path": payload.get("transcript_path") or ""})
        p["pending_captures"] = pending[-10:]
        store.save(p)
    return None


def capture_pending(p: dict) -> bool:
    """Turn the chats that ended since the project was last opened into auto-saves.
    Caller holds store.lock() and saves when this returns True."""
    pending = p.get("pending_captures") or []
    if not pending:
        return False
    retry = []
    for c in pending:
        try:
            auto_capture(p, c)
        except (OSError, ValueError) as e:
            # Often passing (the file still locked or syncing): keep it for the next opening,
            # up to MAX_CAPTURE_TRIES in all, and note why in the error log.
            c = {**c, "tries": c.get("tries", 0) + 1}
            log_error("capture", f"{c.get('transcript_path') or c['session_id']}: {e} (try {c['tries']})")
            if c["tries"] < MAX_CAPTURE_TRIES:
                retry.append(c)
    p["pending_captures"] = retry
    return True


def auto_capture(p: dict, payload: dict, keep: int = 10) -> bool:
    """Chat ended without /threadback-save: keep a free trace of it (no AI). Only messages
    after the last real save count, so a chat that was saved and then ended adds nothing.
    Never touches the next step; the next real save clears these."""
    from . import digest
    from .sources import claude_dir_for

    sid = payload["session_id"]
    path = Path(payload.get("transcript_path") or
                claude_dir_for(payload.get("cwd") or p["folder"]) / f"{sid}.jsonl")
    if not path.is_file():
        return False
    c = digest.chat(path, since=digest.last_saved(p))
    if not c["asked"]:
        return False
    autos = [a for a in p.get("auto_saves", []) if a.get("session_id") != sid]  # resumed chat: replace
    autos.append({"date": store.now(), "session_id": sid, "messages": len(c["asked"]),
                  "asked": [digest._clip(m, 200) for m in c["asked"][-3:]],
                  "last_answer": digest._clip(c["last_answer"], 400)})
    p["auto_saves"] = autos[-keep:]
    return True


def catch_up_hint(p: dict) -> str:
    """Nothing saved yet. The folder's own history (README, commits, old chats) is not loaded
    here: a repo can plant instructions in it, and session-start context is trusted. The user
    asks for it with /threadback-save, which reads the digest as fenced data."""
    return ("\nNothing has been saved for this project yet. If the user asks what they were doing "
            "here, or wants to catch up, suggest /threadback-save: it reads a short digest of the "
            "folder's commits and earlier chats and saves a proper catch-up.")


def brief_for_claude(p: dict, ctx: dict) -> str:
    """Compact project context for Claude. Saved by the user earlier, so it may be stale."""
    from . import digest
    from .sources import ago

    lines = [f"ThreadBack context for project '{p['name']}' (saved in earlier sessions; "
             f"may be out of date, so verify against the code before relying on it).",
             f"Status: {p['status']}. Last activity {ago(ctx['last_activity'])}."]
    if p["next_action"]:
        lines.append(f"Next step the user planned: {p['next_action']}")
    if p["last_state"]:
        lines.append(f"Where they left off: {p['last_state']}")
    if p["blockers"]:
        lines.append("Blockers: " + "; ".join(b["text"] for b in p["blockers"]))
    tasks = [(i, t) for i, t in enumerate(p["tasks"], 1) if not t.get("done")]
    if tasks:
        # All of them, clipped: a cut-off list hides the user's later plans from Claude.
        # /threadback-save writes short tasks; long ones typed by hand end in "..." here.
        lines.append("Unfinished tasks (any ending in '...' are shortened; /threadback shows the full text):")
        lines += [f"{i}. {digest._clip(t['text'], TASK_CHARS)}" for i, t in tasks]
    if p["decisions"]:
        lines.append("Recent decisions:")
        lines += [f"- {d['text']}" + (f" (why: {d['why']})" if d.get("why") else "")
                  for d in p["decisions"][-6:]]
    summaries = [s for s in p["sessions"] if s.get("summary")][-3:]
    if summaries:
        lines.append("Recent sessions:")
        lines += [f"- {s['date'][:10]}: {s['summary']}" for s in summaries]
    # Copied from chats and git, not written for ThreadBack: fenced as data (see redact.fence).
    copied = []
    for a in p.get("auto_saves", [])[-3:]:
        copied.append(f"- Chat ended without /threadback-save, {a['date'][:16]}, {a['messages']} "
                      "message(s). Last asked: " + " / ".join(a["asked"])
                      + (f" | Claude's last answer: {a['last_answer']}" if a["last_answer"] else ""))
    if ctx["git"] and ctx["git"]["uncommitted"]:
        copied.append(f"- Git: {ctx['git']['uncommitted']} uncommitted change(s) on branch {ctx['git']['branch']}.")
    if copied:
        lines.append("Auto-captured since the last save (newer than the saved context):")
        lines.append(fence("\n".join(copied)))
    lines.append("Don't recite all of this unprompted; use it when relevant. If the user asks what they "
                 "were doing, lead with the next step. When they wrap up, suggest /threadback-save.")
    text = redact("\n".join(lines))  # saved fields are masked already; git and chat text too now
    return text if len(text) <= MAX_CONTEXT_CHARS else text[:MAX_CONTEXT_CHARS - 20] + "\n[...truncated]"
