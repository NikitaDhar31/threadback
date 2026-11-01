"""Claude Code plugin hooks (official, documented interface).

Claude Code runs `threadback hook <event>` and passes JSON on stdin with
`session_id` and `cwd`. On session start we record the session and hand Claude
the project's saved context; on session end we record when it finished.

A hook must never break a Claude session: every error is logged to
~/.threadback/hook-errors.log and the hook still exits 0.
"""

from __future__ import annotations

import json
import os
import sys
import traceback

from . import store
from .sources import ago, context

MAX_CONTEXT_CHARS = 3500


def run(event: str) -> None:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        out = session_start(payload) if event == "session-start" else session_end(payload)
        if out:
            print(json.dumps(out, ensure_ascii=False))
    except Exception:
        try:
            log = store.home() / "hook-errors.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as f:
                f.write(f"--- {store.now()} {event}\n{traceback.format_exc()}\n")
        except OSError:
            pass


def _project_for(payload: dict) -> dict | None:
    return store.find_by_folder(payload.get("cwd") or os.getcwd())


def session_start(payload: dict) -> dict:
    p = _project_for(payload)
    if p is None:
        text = ("ThreadBack: this folder is not a tracked project. If the user wants to track it, "
                "/threadback-save registers it.")
    else:
        if payload.get("session_id"):
            store.record_claude_session(p, payload["session_id"])
            store.save(p)
        text = brief_for_claude(p, context(p))
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}


def session_end(payload: dict) -> None:
    p = _project_for(payload)
    if p is not None and payload.get("session_id"):
        store.record_claude_session(p, payload["session_id"], ended=True)
        store.save(p)
    return None


def brief_for_claude(p: dict, ctx: dict) -> str:
    """Compact project context for Claude. Saved by the user earlier, so it may be stale."""
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
        lines.append("Unfinished tasks: " + "; ".join(f"{i}. {t['text']}" for i, t in tasks[:10]))
    if p["decisions"]:
        lines.append("Recent decisions:")
        lines += [f"- {d['text']}" + (f" (why: {d['why']})" if d.get("why") else "")
                  for d in p["decisions"][-6:]]
    summaries = [s for s in p["sessions"] if s.get("summary")][-3:]
    if summaries:
        lines.append("Recent sessions:")
        lines += [f"- {s['date'][:10]}: {s['summary']}" for s in summaries]
    if ctx["git"] and ctx["git"]["uncommitted"]:
        lines.append(f"Git: {ctx['git']['uncommitted']} uncommitted change(s) on {ctx['git']['branch']}.")
    lines.append("Don't recite all of this unprompted; use it when relevant. If the user asks what they "
                 "were doing, lead with the next step. When they wrap up, suggest /threadback-save.")
    text = "\n".join(lines)
    return text if len(text) <= MAX_CONTEXT_CHARS else text[:MAX_CONTEXT_CHARS - 20] + "\n[...truncated]"
