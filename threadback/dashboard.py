"""Static HTML dashboard: a single self-contained file, regenerated on demand."""

from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path

from . import store
from .sources import ago, context, suggest_resume

CSS = """
:root { --bg:#f6f7f9; --card:#fff; --text:#1d2330; --muted:#667085; --line:#e4e7ec;
        --accent:#3557d4; --accent-bg:#eef2ff; --warn:#b42318; --warn-bg:#fef3f2; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#111318; --card:#1a1d24; --text:#e6e8ee; --muted:#98a2b3; --line:#2b303b;
          --accent:#8ea6ff; --accent-bg:#1e2540; --warn:#fda29b; --warn-bg:#3a1d1b; }
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text);
       font:15px/1.5 "Segoe UI", system-ui, sans-serif; }
main { max-width:1040px; margin:0 auto; padding:28px 16px 60px; }
header { display:flex; justify-content:space-between; align-items:baseline; flex-wrap:wrap; gap:8px; }
h1 { font-size:22px; margin:0; } h1 span { color:var(--muted); font-weight:400; font-size:14px; }
h2 { font-size:17px; margin:0; } h3 { font-size:12px; text-transform:uppercase; letter-spacing:.05em;
     color:var(--muted); margin:14px 0 4px; }
.resume { background:var(--accent-bg); border:1px solid var(--accent); border-radius:12px;
          padding:16px 20px; margin:18px 0 24px; }
.resume .label { color:var(--accent); font-weight:600; font-size:12px; text-transform:uppercase; }
.resume .name { font-size:20px; font-weight:600; }
.grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(min(100%, 480px), 1fr)); gap:16px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px 18px; }
.top { display:flex; justify-content:space-between; gap:8px; align-items:baseline; }
.meta { color:var(--muted); font-size:13px; }
.badge { font-size:12px; padding:1px 8px; border-radius:99px; border:1px solid var(--line); }
.badge.blocked { color:var(--warn); background:var(--warn-bg); border-color:var(--warn); }
.badge.active { color:var(--accent); border-color:var(--accent); }
.next { margin-top:8px; padding:8px 10px; border-left:3px solid var(--accent); background:var(--accent-bg); border-radius:4px; }
.blocker { color:var(--warn); }
ul { margin:0; padding-left:20px; } li { margin:2px 0; }
.why { color:var(--muted); font-size:13px; }
a { color:var(--accent); } .links a { margin-right:12px; white-space:nowrap; }
code { font:12px Consolas, monospace; background:var(--bg); border:1px solid var(--line);
       padding:2px 6px; border-radius:4px; word-break:break-all; display:inline-block; max-width:100%; }
details summary { cursor:pointer; color:var(--muted); font-size:13px; margin-top:10px; }
.empty { color:var(--muted); }
"""


def _list(items: list[str], cls: str = "") -> str:
    return "<ul>" + "".join(f'<li class="{cls}">{i}</li>' for i in items) + "</ul>"


def _href(url: str) -> str:
    if url.startswith(("http://", "https://")):
        return url
    try:
        return Path(url).resolve().as_uri()
    except ValueError:
        return url


def card(p: dict, ctx: dict) -> str:
    e = escape
    parts = [f'<section class="card" id="{e(p["slug"])}">',
             f'<div class="top"><h2>{e(p["name"])}</h2>'
             f'<span class="badge {e(p["status"])}">{e(p["status"])}</span></div>',
             f'<div class="meta">Last activity {e(ago(ctx["last_activity"]))}</div>']
    if p["next_action"]:
        parts.append(f'<div class="next"><b>Next:</b> {e(p["next_action"])}</div>')
    if p["last_state"]:
        parts.append(f"<h3>Where you left off</h3><div>{e(p['last_state'])}</div>")
    if p["blockers"]:
        parts.append("<h3>Blockers</h3>" + _list([e(b["text"]) for b in p["blockers"]], "blocker"))
    tasks = [t for t in p["tasks"] if not t.get("done")]
    if tasks:
        parts.append("<h3>Unfinished</h3>" + _list([e(t["text"]) for t in tasks]))
    if p["decisions"]:
        items = [f"{e(d['text'])}" + (f'<div class="why">why: {e(d["why"])}</div>' if d.get("why") else "")
                 for d in p["decisions"][-4:]]
        parts.append("<h3>Recent decisions</h3>" + _list(items))

    signals = []
    git = ctx["git"]
    if git:
        s = f"git <b>{e(git['branch'])}</b>: "
        s += (f"last commit {e(ago(git['last_commit']))} - {e(git['last_commit_msg'])}"
              if git["last_commit"] else "no commits yet")
        signals.append(s)
    for s in ctx["claude"][:2]:
        title = s["title"] or s["last_prompt"][:80] or "(untitled)"
        signals.append(f"Claude Code: {e(title)} <span class='why'>({e(ago(s['last_active']))})</span>")
    if signals:
        parts.append("<h3>Detected activity</h3>" + _list(signals))
    if ctx["claude"] and not store.has_control_chars(p["folder"]):
        cmd = store.resume_command(p["folder"], ctx["claude"][0]["id"])
        parts.append(f"<div style='margin-top:6px'><code>{e(cmd)}</code></div>")

    links = []
    if p["folder"]:
        links.append(f'<a href="{e(_href(p["folder"]))}">Folder</a>')
    links += [f'<a href="{e(_href(l["url"]))}">{e(l["label"])}</a>' for l in p["links"]]
    if links:
        parts.append(f'<h3>Open</h3><div class="links">{"".join(links)}</div>')

    extra = []
    if p["ideas"]:
        extra.append("<h3>Ideas not yet implemented</h3>" + _list([e(i["text"]) for i in p["ideas"]]))
    if p["sessions"]:
        extra.append("<h3>Session log</h3>" + _list(
            [f"<b>{e(s['date'][:16])}</b> {e(s.get('summary') or '')}" for s in reversed(p["sessions"][-8:])]))
    if extra:
        parts.append("<details><summary>Ideas and history</summary>" + "".join(extra) + "</details>")
    parts.append("</section>")
    return "\n".join(parts)


def render(projects: list[dict]) -> str:
    pcs = sorted(((p, context(p)) for p in projects),
                 key=lambda pc: pc[1]["last_activity"] or datetime.min, reverse=True)
    live = [pc for pc in pcs if pc[0]["status"] != "done"]
    done = [pc for pc in pcs if pc[0]["status"] == "done"]
    pick = suggest_resume(live)

    body = [f"<header><h1>ThreadBack <span>{len(live)} open project(s)</span></h1>"
            f'<span class="meta">Generated {datetime.now():%a %d %b %Y, %H:%M}</span></header>']
    if pick:
        p, _ = pick
        body.append(f'<div class="resume"><div class="label">Resume this</div>'
                    f'<div class="name"><a href="#{escape(p["slug"])}">{escape(p["name"])}</a></div>'
                    f'<div>{escape(p["next_action"] or "No next action saved - check the card below.")}</div></div>')
    if not projects:
        body.append('<p class="empty">No projects yet. Run <code>threadback new "My Project" --folder PATH</code>.</p>')
    body.append('<div class="grid">' + "".join(card(p, c) for p, c in live) + "</div>")
    if done:
        body.append("<details><summary>Done projects</summary><div class='grid'>"
                    + "".join(card(p, c) for p, c in done) + "</div></details>")
    body.append('<p class="meta" style="margin-top:28px">Before you stop: double-click '
                '<b>ThreadBack</b> on your desktop and click <b>Save</b> on the project.</p>')
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>ThreadBack</title><style>{CSS}</style></head><body><main>"
            + "\n".join(body) + "</main></body></html>")


def write() -> Path:
    path = store.home() / "dashboard.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(store.load_all()), encoding="utf-8")
    return path
