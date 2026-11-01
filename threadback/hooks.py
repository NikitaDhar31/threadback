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

from pathlib import Path

from . import digest, store
from .sources import ago, claude_dir_for, context

MAX_CONTEXT_CHARS = 3500
DIGEST_CHARS = 1800


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
                "/threadback-save registers it and can catch up from the folder's history.")
    else:
        if payload.get("session_id"):
            store.record_claude_session(p, payload["session_id"])
            store.save(p)
        text = brief_for_claude(p, context(p))
        if not p["sessions"] and not p["next_action"]:
            text += catch_up_hint(p)
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}


def session_end(payload: dict) -> None:
    p = _project_for(payload)
    if p is not None and payload.get("session_id"):
        store.record_claude_session(p, payload["session_id"], ended=True)
        store.save(p)
        if auto_capture(p, payload):
            store.save(p)
    return None


def auto_capture(p: dict, payload: dict, keep: int = 10) -> bool:
    """Chat ended without /threadback-save: keep a free trace of it (no AI). Only messages
    after the last real save count, so a chat that was saved and then ended adds nothing.
    Never touches the next step; the next real save clears these."""
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
    """Nothing saved yet: hand Claude a short digest so it still knows what happened here."""
    d = digest.build(p["folder"], max_chars=DIGEST_CHARS, n_chats=2, messages=4) if p["folder"] else ""
    return ("\nNothing has been saved for this project yet. Automatic digest of the folder "
            "(commits and earlier Claude chats, may be partial):\n" + d +
            "\nIf the user asks what they were doing, answer from this. Suggest /threadback-save "
            "to save a proper catch-up.")


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
    autos = p.get("auto_saves", [])[-3:]
    if autos:
        lines.append("Chats that ended without /threadback-save (auto-captured; newer than the saved context):")
        for a in autos:
            lines.append(f"- {a['date'][:16]}, {a['messages']} message(s). Last asked: "
                         + " / ".join(a["asked"]) + (f" | Claude's last answer: {a['last_answer']}"
                                                    if a["last_answer"] else ""))
    if ctx["git"] and ctx["git"]["uncommitted"]:
        lines.append(f"Git: {ctx['git']['uncommitted']} uncommitted change(s) on {ctx['git']['branch']}.")
    lines.append("Don't recite all of this unprompted; use it when relevant. If the user asks what they "
                 "were doing, lead with the next step. When they wrap up, suggest /threadback-save.")
    text = "\n".join(lines)
    return text if len(text) <= MAX_CONTEXT_CHARS else text[:MAX_CONTEXT_CHARS - 20] + "\n[...truncated]"
