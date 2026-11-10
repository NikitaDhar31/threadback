"""ThreadBack command line interface."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

from . import dashboard, digest, hooks, startup, store
from .redact import fence, redact
from .sources import ago, context, last_work, suggest_resume


# ---------------------------------------------------------------- helpers

def _split_pair(value: str) -> tuple[str, str]:
    """'text | extra' -> ('text', 'extra')."""
    left, _, right = value.partition("|")
    return left.strip(), right.strip()


def _open_target(target: str) -> None:
    if target.startswith(("http://", "https://")):
        webbrowser.open(target)
    elif hasattr(os, "startfile"):
        os.startfile(target)  # type: ignore[attr-defined]  # Windows: Explorer / default app
    else:
        subprocess.run(["xdg-open", target], check=False)


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        answer = ""
    return answer or default


def _ask_lines(prompt: str) -> list[str]:
    print(f"{prompt} (one per line, empty line to finish)")
    lines = []
    while True:
        try:
            line = input("  > ").strip()
        except EOFError:
            break
        if not line:
            break
        lines.append(line)
    return lines


def _numbers(value: str) -> list[int]:
    return [int(x) for x in value.replace(",", " ").split() if x.isdigit()]


def resolve(name: str | None, register: bool = False) -> dict:
    """Project by name, or else the one for the current folder (optionally registering it)."""
    if name:
        return store.find(name)
    cwd = os.getcwd()
    p = store.find_by_folder(cwd)
    if p:
        return p
    if not register:
        raise LookupError(f"{cwd} isn't a ThreadBack project yet. Run /threadback-save in a Claude Code chat here (or 'save' from the command line) to start tracking it.")
    p = store.register_folder(cwd)
    print(f"Registered new project '{p['name']}' for {p['folder']}")
    return p


def open_tasks(project: dict) -> list[tuple[int, dict]]:
    """Open tasks with their 1-based number in the full task list."""
    return [(i, t) for i, t in enumerate(project["tasks"], 1) if not t.get("done")]


# ---------------------------------------------------------------- brief

def brief(project: dict, ctx: dict) -> str:
    """Plain-text context-recovery brief for one project."""
    out = []
    p = project
    out.append(f"== {store.display_name(p['name'])}  [{p['status']}]  last activity {ago(ctx['last_activity'])}")
    if p["next_action"]:
        out.append(f"\nNEXT ACTION: {p['next_action']}")
    if p["last_state"]:
        out.append(f"\nWhere you left off:\n  {p['last_state']}")
    if not p["sessions"] and not p["next_action"]:
        seen = last_work(ctx)  # a commit message or chat title: the folder's text, fenced as data
        out.append("\nNothing saved yet." + (" Seen in the folder:\n" + fence(seen) if seen else "") +
                   "\n  Run /threadback-save in a Claude Code chat here to catch up from its history.")
    if p["sessions"]:
        s = p["sessions"][-1]
        out.append(f"\nLast saved session ({s['date']}):\n  {s.get('summary') or '-'}")
    autos = p.get("auto_saves", [])
    if autos:
        out.append(f"\nChats with unsaved messages ({len(autos)}, auto-captured):")
        out.append(fence("\n".join(f"  - {a['date'][:16]}  last asked: {a['asked'][-1] if a['asked'] else '-'}"
                                    for a in autos[-3:])))
    if p["blockers"]:
        out.append("\nBlockers:")
        out += [f"  {i}. {b['text']}" for i, b in enumerate(p["blockers"], 1)]
    tasks = open_tasks(p)
    if tasks:
        out.append("\nUnfinished tasks:")
        out += [f"  {i}. {t['text']}" for i, t in tasks]
    if p["decisions"]:
        out.append("\nRecent decisions:")
        for d in p["decisions"][-5:]:
            why = f"\n       why: {d['why']}" if d.get("why") else ""
            out.append(f"  - {d['text']}  ({d['date'][:10]}){why}")
    if p["ideas"]:
        out.append("\nIdeas not yet implemented:")
        out += [f"  - {x['text']}" for x in p["ideas"]]
    # Branch, commit message and chat titles come from the folder, not the user: fenced as data.
    git = ctx["git"]
    if git:
        line = f"Git ({git['branch']}): "
        if git["last_commit"]:
            line += f"last commit {ago(git['last_commit'])} - {git['last_commit_msg']}"
        else:
            line += "no commits yet"
        out.append("\n" + fence(line))
    if ctx["claude"]:
        out.append("\nRecent Claude Code sessions in this folder:")
        out.append(fence("\n".join(f"  - {s['title'] or s['last_prompt'][:70] or '(untitled)'}  "
                                    f"({ago(s['last_active'])})" for s in ctx["claude"])))
        if not store.has_control_chars(p["folder"]):
            out.append("    resume latest: " + store.resume_command(p["folder"], ctx["claude"][0]["id"]))
    if p["links"] or p["folder"]:
        out.append("\nLinks (threadback open <project> <label>):")
        if p["folder"]:  # the folder's name is not the user's text either: fenced as data
            out.append(fence(f"  - folder: {p['folder']}"))
        out += [f"  - {l['label']}: {l['url']}" for l in p["links"]]
    return redact("\n".join(out))  # /threadback hands this to Claude: mask git and chat text too


# ---------------------------------------------------------------- commands

def cmd_new(a) -> None:
    with store.lock():
        _new(a)


def _new(a) -> None:
    if store.path_for(store.slugify(a.name)).exists():
        sys.exit(f"Project '{a.name}' already exists.")
    p = store.new_project(a.name, folder=str(Path(a.folder).resolve()) if a.folder else None)
    if a.next:
        p["next_action"] = a.next
    for link in a.link or []:
        label, url = _split_pair(link)
        p["links"].append({"label": label, "url": url})
    print(f"Created {store.save(p)}")


def cmd_list(a) -> None:
    projects = store.load_all()
    if not projects:
        print('No projects yet. Start with: threadback new "My Project" --folder <path>')
        return
    pcs = sorted(((p, context(p)) for p in projects),
                 key=lambda pc: pc[1]["last_activity"] or datetime.min, reverse=True)
    if not a.all:
        pcs = [pc for pc in pcs if pc[0]["status"] != "done"]
    pick = suggest_resume(pcs)
    if pick:
        print(f"Resume: {store.display_name(pick[0]['name'])} -> "
              f"{pick[0]['next_action'] or '(no next action saved)'}\n")
    # /threadback-all hands this list to Claude: names as display_name makes them.
    w = max(len(store.display_name(p["name"])) for p, _ in pcs) if pcs else 10
    for p, ctx in pcs:
        slug = f"[{p['slug']}]  " if a.slugs else ""
        print(f"  {slug}{store.display_name(p['name']):<{w}}  {p['status']:<8} {ago(ctx['last_activity']):<16} "
              f"next: {p['next_action'] or '-'}")


def cmd_show(a) -> None:
    with store.lock():
        try:
            p = resolve(a.name)
        except LookupError as e:
            if a.name:
                raise
            # A new folder is not an error: /threadback-save runs this first, and a failed
            # step there looks like ThreadBack is broken.
            print(e)
            return
        if hooks.capture_pending(p):  # chats that ended since the project was last opened
            store.save(p)
    print(brief(p, context(p)))


# --json keys -> save options. Lists may hold "text | why" strings or {"text", "why"} objects.
_JSON_KEYS = {"summary": "summary", "state": "state", "next": "next", "status": "status",
              "decisions": "decision", "tasks": "task", "done": "done", "blockers": "blocker",
              "unblock": "unblock", "ideas": "idea", "links": "link"}


def _json_item(key: str, attr: str, item) -> str:
    """One list entry of --json: text, or {"text", "why"} ({"label", "url"} for links)."""
    if isinstance(item, str):
        return item
    first, second = ("label", "url") if attr == "link" else ("text", "why")
    if isinstance(item, dict) and isinstance(item.get(first), str) and \
            isinstance(item.get(second, ""), str) and set(item) <= {first, second}:
        return f"{item[first]} | {item.get(second, '')}" if item.get(second) else item[first]
    raise ValueError(f'--json: each of {key} must be text or {{"{first}": ..., "{second}": ...}}')


_LINE_KEYS = {"summary": "summary", "state": "state", "next": "next", "status": "status",
              "decision": "decision", "task": "task", "done": "done", "blocker": "blocker",
              "unblock": "unblock", "idea": "idea", "link": "link"}


def _read_lines_save(a, text: str) -> None:
    """Fill the save options from 'key: value' lines (list keys may repeat). /threadback-save
    sends these: Claude Code always asks before a command holding JSON's brace-and-quote."""
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if not sep or key not in _LINE_KEYS:
            raise ValueError(f"line {n}: expected 'key: value' with key one of {', '.join(_LINE_KEYS)}")
        attr = _LINE_KEYS[key]
        if attr in ("summary", "state", "next", "status"):
            if getattr(a, attr):
                raise ValueError(f"line {n}: {key} given twice")
            setattr(a, attr, value)
        elif attr in ("done", "unblock"):
            nums = value.replace(",", " ").split()
            if not all(x.isdigit() for x in nums):
                raise ValueError(f"line {n}: {key} must be task numbers, e.g. '{key}: 2 5'")
            setattr(a, attr, (getattr(a, attr) or []) + [int(x) for x in nums])
        elif value:
            setattr(a, attr, (getattr(a, attr) or []) + [value])
    if a.status and a.status not in store.STATUSES:
        raise ValueError(f"status must be one of {', '.join(store.STATUSES)}")


def _read_json_save(a, text: str | None = None) -> None:
    """Fill the save options from JSON on stdin. Slash commands pass text written from the
    chat this way, through a quoted heredoc, so nothing in it can reach a shell."""
    try:
        data = json.loads((sys.stdin.read() if text is None else text) or "{}")
    except json.JSONDecodeError as e:
        raise ValueError(f"--json: stdin is not valid JSON ({e})")
    if not isinstance(data, dict):
        raise ValueError("--json: stdin must hold one JSON object")
    unknown = sorted(set(data) - set(_JSON_KEYS))
    if unknown:
        raise ValueError(f"--json: unknown keys {', '.join(unknown)}")
    for key, attr in _JSON_KEYS.items():
        if key not in data or data[key] in (None, "", []):
            continue
        value = data[key]
        if attr in ("summary", "state", "next", "status"):
            if not isinstance(value, str):
                raise ValueError(f"--json: {key} must be text")
            setattr(a, attr, value)
            continue
        items = value if isinstance(value, list) else [value]
        if attr in ("done", "unblock"):
            # bool is an int in Python, but true/false is never a task number
            if not all(isinstance(n, int) and not isinstance(n, bool) for n in items):
                raise ValueError(f"--json: {key} must be task numbers, e.g. [2, 5]")
            setattr(a, attr, items)
            continue
        setattr(a, attr, [_json_item(key, attr, i) for i in items])
    if a.status and a.status not in store.STATUSES:
        raise ValueError(f"--json: status must be one of {', '.join(store.STATUSES)}")


def cmd_save(a) -> None:
    if a.json:
        _read_json_save(a)
    flags = any([a.summary, a.state, a.next, a.decision, a.task, a.done, a.blocker,
                 a.unblock, a.idea, a.status, a.link])
    if not flags and not sys.stdin.isatty():
        sys.exit("Nothing to save. Pass flags (see --help) or run in a terminal for prompts.")
    with store.lock():  # registering a new folder is a write too: two first saves make one project
        p = resolve(a.name, register=True)
    if not flags:
        _interactive_save(p, a)  # prompts run unlocked, so a slow answer never holds up the hooks
    with store.lock():
        _apply_save(store.reload(p), a)


def _apply_save(p: dict, a) -> None:
    s = store.add_session(p, a.summary or "", a.next or "")

    if a.state:
        p["last_state"] = a.state
    if a.status:
        p["status"] = a.status
    for d in a.decision or []:
        text, why = _split_pair(d)
        p["decisions"].append({"date": s["date"], "text": text, "why": why})
    for t in a.task or []:
        p["tasks"].append({"text": t, "done": False, "added": s["date"], "done_on": ""})
    for n in a.done or []:
        if 1 <= n <= len(p["tasks"]):
            p["tasks"][n - 1].update(done=True, done_on=s["date"])
        else:
            print(f"warning: no task number {n}")
    for n in sorted(set(a.unblock or []), reverse=True):
        if 1 <= n <= len(p["blockers"]):
            p["blockers"].pop(n - 1)
        else:
            print(f"warning: no blocker number {n}")
    for b in a.blocker or []:
        p["blockers"].append({"text": b, "added": s["date"]})
    for i in a.idea or []:
        p["ideas"].append({"text": i, "added": s["date"]})
    for link in a.link or []:
        label, url = _split_pair(link)
        p["links"].append({"label": label, "url": url})
    store.save(p)
    print(f"Saved context for {p['name']}.")


def cmd_task(a) -> None:
    """One task edit, as the panel's buttons make them. N is the number in the full list."""
    with store.lock():
        _task(resolve(a.project), a)


def _task(p: dict, a) -> None:
    tasks = p["tasks"]
    if a.action == "add":
        if not a.arg.strip():
            raise ValueError("Type the task to add.")
        tasks.append({"text": a.arg.strip(), "done": False, "added": store.now(), "done_on": ""})
    else:
        n = int(a.arg) if a.arg.isdigit() else 0
        if not 1 <= n <= len(tasks):
            raise LookupError(f"No task number {a.arg} in {p['name']}.")
        t = tasks[n - 1]
        if a.action == "done":
            t.update(done=True, done_on=store.now())
        elif a.action == "undo":
            t.update(done=False, done_on="")
        elif a.action == "remove":
            tasks.pop(n - 1)
        elif a.action == "top":
            tasks.insert(0, tasks.pop(n - 1))
        elif a.action == "next":
            # The task becomes the next step; the old next step goes back on top of the list.
            tasks.pop(n - 1)
            if p["next_action"]:
                tasks.insert(0, {"text": p["next_action"], "done": False, "added": store.now(), "done_on": ""})
            p["next_action"] = t["text"]
    store.save(p)
    print(f"Updated tasks for {p['name']}.")


def _interactive_save(p: dict, a) -> None:
    """Fill the save arguments by prompting. Every question can be skipped."""
    print(f"Saving session for {p['name']} - press Enter to skip any question.\n")
    a.summary = _ask("What did you get done")
    a.state = _ask("Where did you leave off (current state)", p["last_state"])
    if a.state == p["last_state"]:
        a.state = None
    tasks = open_tasks(p)
    if tasks:
        print("Open tasks:")
        for i, t in tasks:
            print(f"  {i}. {t['text']}")
        a.done = _numbers(_ask("Numbers of tasks you finished (e.g. 1 3)"))
    a.task = _ask_lines("New unfinished tasks")
    a.decision = _ask_lines("Decisions made, as 'decision | why'")
    if p["blockers"]:
        for i, b in enumerate(p["blockers"], 1):
            print(f"  blocker {i}. {b['text']}")
        a.unblock = _numbers(_ask("Numbers of blockers now resolved"))
    a.blocker = _ask_lines("New blockers")
    a.idea = _ask_lines("Ideas discussed but not implemented")
    a.link = _ask_lines("Conversation links used, as 'label | url'")
    a.next = _ask("Next action when you come back", p["next_action"])
    status = _ask(f"Status ({'/'.join(store.STATUSES)})", p["status"])
    a.status = status if status in store.STATUSES and status != p["status"] else None


def cmd_digest(a) -> None:
    p = store.find(a.name) if a.name else store.find_by_folder(os.getcwd())
    folder = p["folder"] if p and p["folder"] else os.getcwd()
    since = None if a.all or p is None else digest.last_saved(p)
    print(digest.build(folder, since, n_chats=a.chats))


def cmd_here(a) -> None:
    """The slash commands' only entry point, approved in their allowed-tools. It takes no
    project name and no save flags, so planted text can't point an approved call at another
    project or widen what it does: the project for the folder this Claude Code session
    started in, save values on stdin as 'key: value' lines (or one JSON object).

    That folder comes from the SessionStart hook, never from the shell's current folder:
    Claude may `cd` into a subfolder without asking, and a project nested in this one, or
    in an added working directory, must stay out of reach."""
    if a.all and a.action != "digest":
        raise ValueError("--all is for 'here digest' only.")
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    root = store.session_root(session_id) if session_id else None
    if not root:
        raise LookupError("ThreadBack can't tell which folder this Claude Code chat started in "
                          "(no SessionStart record for it), so 'tb here' won't guess. Start a new "
                          "chat in the project folder, or run 'tb show' / 'tb save' in a terminal.")
    try:
        os.chdir(root)
    except OSError as e:
        raise LookupError(f"This chat's folder {root} can't be opened: {e.strerror}")
    if a.action == "show":
        cmd_show(argparse.Namespace(name=None))
    elif a.action == "digest":
        cmd_digest(argparse.Namespace(name=None, all=a.all, chats=4))
    else:
        save = argparse.Namespace(name=None, json=False, **{k: None for k in _SAVE_FLAGS})
        text = sys.stdin.read()
        if text.lstrip().startswith("{"):
            _read_json_save(save, text)
        else:
            _read_lines_save(save, text)
        cmd_save(save)


_SAVE_FLAGS = ("summary", "state", "next", "decision", "task", "done", "blocker", "unblock",
               "idea", "status", "link")


def cmd_link(a) -> None:
    with store.lock():
        _link(store.find(a.name), a)


def _link(p: dict, a) -> None:
    if a.label.lower() == "folder":
        p["folder"] = str(Path(a.url).resolve())
    else:
        p["links"] = [l for l in p["links"] if l["label"] != a.label]
        p["links"].append({"label": a.label, "url": a.url})
    store.save(p)
    print(f"Linked {a.label} -> {a.url}")


def cmd_open(a) -> None:
    p = store.find(a.name)
    if not a.what:
        targets = (["folder"] if p["folder"] else []) + [l["label"] for l in p["links"]]
        if not targets:
            sys.exit(f"No links for {p['name']}. Add one: threadback link \"{p['name']}\" <label> <url>")
        print("Open which? " + ", ".join(targets) + "  (or 'claude' for the latest Claude Code session)")
        return
    what = a.what.lower()
    if what == "folder":
        if not p["folder"]:
            sys.exit("No folder set.")
        _open_target(p["folder"])
        return
    if what == "claude":
        if not p["folder"]:
            sys.exit("No folder set.")
        if store.has_control_chars(p["folder"]):
            sys.exit("This project's folder name has line breaks or control characters, so no "
                     "command is shown for it. Open the folder yourself and start claude there.")
        ctx = context(p)
        print(store.resume_command(p["folder"], ctx["claude"][0]["id"] if ctx["claude"] else None))
        return
    hits = [l for l in p["links"] if what in l["label"].lower()]
    if not hits:
        sys.exit(f"No link matching '{a.what}'.")
    for l in hits:
        print(f"Opening {l['label']}: {l['url']}")
        _open_target(l["url"])


def cmd_edit(a) -> None:
    p = store.find(a.name)
    path = str(store.path_for(p["slug"]))
    editor = os.environ.get("EDITOR")
    if editor:
        subprocess.run([editor, path], check=False)
    else:
        _open_target(path)
    print(path)


def cmd_dashboard(a) -> None:
    path = dashboard.write()
    print(f"Dashboard written to {path}")
    if not a.no_open:
        webbrowser.open(path.as_uri())


def cmd_welcome(a) -> None:
    from . import popup  # tkinter is only needed here
    popup.show()


def cmd_startup(a) -> None:
    if a.action == "install":
        for p in startup.install():
            print(f"Installed: {p}")
        print("The welcome window will appear when you log in, or double-click ThreadBack on your desktop.")
    elif a.action == "remove":
        print("Removed." if startup.remove() else "Was not installed.")
    else:
        p = startup.script_path()
        print(f"Installed at {p}" if p.exists() else "Not installed.")


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="threadback", description="Recover your working context across projects.")
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("new", help="create a project")
    s.add_argument("name")
    s.add_argument("--folder", help="local project folder (enables git + Claude Code detection)")
    s.add_argument("--next", help="first next action")
    s.add_argument("--link", action="append", help="'label | url' (repeatable)")
    s.set_defaults(fn=cmd_new)

    s = sub.add_parser("list", help="all projects, most recent first (default command)")
    s.add_argument("--all", action="store_true", help="include done projects")
    s.add_argument("--slugs", action="store_true", help="show each project's slug (a-z, 0-9, -)")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("show", help="full context-recovery brief (default: project for this folder)")
    s.add_argument("name", nargs="?")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("save", help="save a session summary (interactive without flags); "
                                    "without a name, uses/registers this folder's project")
    s.add_argument("name", nargs="?")
    s.add_argument("--summary", "-s", help="what you got done")
    s.add_argument("--state", help="where you left off")
    s.add_argument("--next", "-n", help="next action")
    s.add_argument("--decision", "-d", action="append", help="'decision | why' (repeatable)")
    s.add_argument("--task", "-t", action="append", help="new unfinished task (repeatable)")
    s.add_argument("--done", type=int, action="append", help="task number finished (repeatable)")
    s.add_argument("--blocker", "-b", action="append", help="new blocker (repeatable)")
    s.add_argument("--unblock", type=int, action="append", help="blocker number resolved")
    s.add_argument("--idea", "-i", action="append", help="idea not yet implemented")
    s.add_argument("--status", choices=store.STATUSES)
    s.add_argument("--link", action="append", help="'label | url' of a conversation used")
    s.add_argument("--json", action="store_true",
                   help="read the values as one JSON object on stdin (keys: summary, state, next, "
                        "status, decisions, tasks, done, blockers, unblock, ideas, links)")
    s.set_defaults(fn=cmd_save)

    s = sub.add_parser("task", help="edit one task: add TEXT, or done/undo/remove/top/next N "
                                    "(N = number in the full list; next makes it the next step)")
    s.add_argument("action", choices=["add", "done", "undo", "remove", "top", "next"])
    s.add_argument("arg", help="task text for add, else the task number")
    s.add_argument("--project", "-p", help="project name or slug (default: this folder's)")
    s.set_defaults(fn=cmd_task)

    s = sub.add_parser("digest", help="short summary of commits and Claude chats in the folder "
                                      "(since the last save; free, no AI)")
    s.add_argument("name", nargs="?")
    s.add_argument("--all", action="store_true", help="ignore the last save; summarize everything recent")
    s.add_argument("--chats", type=int, default=4, help="how many recent Claude chats to include")
    s.set_defaults(fn=cmd_digest)

    s = sub.add_parser("here", allow_abbrev=False,
                       help="this folder's project only, for the slash commands: show, "
                            "digest [--all], or save (key: value lines or JSON on stdin)")
    s.add_argument("action", choices=["show", "digest", "save"])
    s.add_argument("--all", action="store_true", help="digest: ignore the last save")
    s.set_defaults(fn=cmd_here)

    s = sub.add_parser("link", help="add/replace a link (ChatGPT, Claude, repo...), or set 'folder'")
    s.add_argument("name")
    s.add_argument("label")
    s.add_argument("url")
    s.set_defaults(fn=cmd_link)

    s = sub.add_parser("open", help="open a project's folder or link by label")
    s.add_argument("name")
    s.add_argument("what", nargs="?")
    s.set_defaults(fn=cmd_open)

    s = sub.add_parser("edit", help="open the project's JSON file in your editor")
    s.add_argument("name")
    s.set_defaults(fn=cmd_edit)

    s = sub.add_parser("dashboard", help="write the HTML dashboard and open it")
    s.add_argument("--no-open", action="store_true")
    s.set_defaults(fn=cmd_dashboard)

    s = sub.add_parser("hook", help="Claude Code plugin hook entry point (reads JSON on stdin)")
    s.add_argument("event", choices=["session-start", "session-end"])
    s.set_defaults(fn=lambda a: hooks.run(a.event))

    s = sub.add_parser("welcome", help="show the welcome-back window (what to resume)")
    s.set_defaults(fn=cmd_welcome)

    s = sub.add_parser("startup", help="welcome window at Windows login + desktop icon")
    s.add_argument("action", choices=["install", "remove", "status"])
    s.set_defaults(fn=cmd_startup)
    return ap


def main(argv: list[str] | None = None) -> None:
    # Windows reads pipes in the local code page; `save --json` and hooks send UTF-8
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    if not args.cmd:
        args = build_parser().parse_args(["list"])
    try:
        args.fn(args)
    except (LookupError, ValueError) as e:
        sys.exit(str(e))
