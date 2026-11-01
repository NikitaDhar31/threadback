"""'Welcome back' window: what to resume, plus one-click saving per project.

Shown at login and from the desktop icon. Native (tkinter), so it needs no
browser and no terminal.
"""

from __future__ import annotations

import webbrowser
from datetime import datetime

from . import dashboard, store
from .sources import ago, context, last_work, suggest_resume

INK, MUTED, ACCENT = "#1d2330", "#667085", "#3557d4"


def summary() -> dict:
    """What the window shows: the project to resume plus the other open ones."""
    pcs = [(p, context(p)) for p in store.load_all() if p["status"] != "done"]
    pick = suggest_resume(pcs)
    others = sorted((pc for pc in pcs if pc is not pick),
                    key=lambda pc: pc[1]["last_activity"] or datetime.min, reverse=True)
    return {"pick": pick, "others": others}


def unsaved(p: dict) -> str:
    n = len(p.get("auto_saves", []))
    return f"  - {n} chat{'s' if n != 1 else ''} ended without saving" if n else ""


def quick_save(slug: str, did: str, next_step: str) -> dict:
    """Save from the window: reload first so we never overwrite newer edits."""
    p = store.find(slug)
    store.add_session(p, did, next_step)
    store.save(p)
    return p


def show() -> None:
    import tkinter as tk

    root = tk.Tk()
    root.title("ThreadBack - welcome back")
    root.configure(padx=22, pady=18)
    root.attributes("-topmost", True)  # appear above the browser/startup clutter...
    root.after(1500, lambda: root.attributes("-topmost", False))  # ...but don't stay pinned
    body = tk.Frame(root)
    body.pack(fill="both", expand=True)

    def label(parent, text, size=10, bold=False, color=INK, pad=(0, 0), wrap=480):
        w = tk.Label(parent, text=text, font=("Segoe UI", size, "bold" if bold else "normal"),
                     fg=color, justify="left", anchor="w", wraplength=wrap)
        w.pack(fill="x", pady=pad)
        return w

    def button(parent, text, command, side="right"):
        tk.Button(parent, text=text, font=("Segoe UI", 9), padx=8, command=command).pack(side=side, padx=(6, 0))

    def save_dialog(p: dict) -> None:
        d = tk.Toplevel(root)
        d.title(f"Save progress - {p['name']}")
        d.configure(padx=18, pady=14)
        d.transient(root)
        d.grab_set()
        label(d, p["name"], 13, True)
        label(d, "What did you do?", 9, True, MUTED, pad=(8, 2))
        did = tk.Text(d, width=56, height=3, font=("Segoe UI", 10), wrap="word")
        did.pack(fill="x")
        label(d, "Next step - what should you do when you come back? (optional)", 9, True, MUTED, pad=(10, 2))
        nxt = tk.Entry(d, width=56, font=("Segoe UI", 10))
        nxt.insert(0, p["next_action"])
        nxt.pack(fill="x")
        status = label(d, "", 9, color=MUTED, pad=(6, 0))
        if not p["sessions"]:
            label(d, "Don't remember? Open a Claude Code chat in this folder and run /threadback-save: "
                     "it catches up from the folder's commits and earlier chats.", 9, color=MUTED, wrap=440)

        def do_save(_event=None):
            text, step = did.get("1.0", "end").strip(), nxt.get().strip()
            if not text and step == p["next_action"]:
                status.config(text="Type what you did, a next step, or both - either one is enough.", fg="#b42318")
                return
            quick_save(p["slug"], text, step)
            d.destroy()
            render()

        row = tk.Frame(d)
        row.pack(fill="x", pady=(12, 0))
        button(row, "Save", do_save)
        button(row, "Cancel", d.destroy)
        nxt.bind("<Return>", do_save)
        d.bind("<Escape>", lambda _e: d.destroy())
        (nxt if p["next_action"] == "" else did).focus_set()

    def render() -> None:
        for w in body.winfo_children():
            w.destroy()
        data = summary()

        if data["pick"]:
            p, ctx = data["pick"]
            label(body, "RESUME THIS", 9, True, ACCENT)
            top = tk.Frame(body)
            top.pack(fill="x")
            tk.Label(top, text=p["name"], font=("Segoe UI", 16, "bold"), fg=INK).pack(side="left")
            button(top, "Save progress", lambda p=p: save_dialog(p))
            label(body, f"Next: {p['next_action'] or 'no next step saved yet'}", 11, pad=(4, 0))
            if p["last_state"]:
                label(body, f"Where you left off: {p['last_state']}", color="#555", pad=(2, 0))
            label(body, f"Last activity {ago(ctx['last_activity'])}{unsaved(p)}", 9, color=MUTED, pad=(2, 10))
        else:
            label(body, "No recent activity found.", 12, True, pad=(0, 10))

        if data["others"]:
            label(body, "Other open projects", 9, True, MUTED)
            for p, ctx in data["others"]:
                row = tk.Frame(body)
                row.pack(fill="x", pady=1)
                hint = f"next: {p['next_action']}" if p["next_action"] else last_work(ctx) or "next: -"
                text = f"{p['name']}  ({ago(ctx['last_activity'])}){unsaved(p)}\n{hint}"
                tk.Label(row, text=text, font=("Segoe UI", 9), fg=INK, justify="left", anchor="w",
                         wraplength=400).pack(side="left", fill="x", expand=True)
                button(row, "Save", lambda p=p: save_dialog(p))

        label(body, "Stopping work? Click Save and write what you did, your next step, or both.",
              9, color=MUTED, pad=(12, 0))
        bottom = tk.Frame(body)
        bottom.pack(fill="x", pady=(12, 0))
        button(bottom, "Close", root.destroy)
        button(bottom, "Open dashboard",
               lambda: (webbrowser.open(dashboard.write().as_uri()), root.destroy()), side="left")

    dashboard.write()
    render()
    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2
    y = max(0, (root.winfo_screenheight() - root.winfo_reqheight()) // 4)
    root.geometry(f"+{x}+{y}")
    root.focus_force()
    root.mainloop()
