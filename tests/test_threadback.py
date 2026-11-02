import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from threadback import cli, dashboard, sources, startup, store


class TempHome(unittest.TestCase):
    """Every test gets its own data dir, Claude dir and Startup dir."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        env = {
            "THREADBACK_HOME": str(self.tmp / "home"),
            "THREADBACK_CLAUDE_DIR": str(self.tmp / "claude"),
            "THREADBACK_STARTUP_DIR": str(self.tmp / "startup"),
            "THREADBACK_DESKTOP_DIR": str(self.tmp / "desktop"),
        }
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.main(list(argv))
        return buf.getvalue()


class StoreTests(TempHome):
    def test_roundtrip_and_find(self):
        store.save(store.new_project("Garden Notes"))
        store.save(store.new_project("GroveTool"))
        self.assertEqual(store.find("garden-notes")["name"], "Garden Notes")
        self.assertEqual(store.find("garden")["name"], "Garden Notes")
        self.assertEqual(store.find("tool")["name"], "GroveTool")
        with self.assertRaises(LookupError):
            store.find("g")  # ambiguous prefix
        with self.assertRaises(LookupError):
            store.find("nothing")

    def test_hand_edited_file_missing_fields_still_loads(self):
        (store.projects_dir() / "manual.json").write_text('{"name": "Manual"}', encoding="utf-8")
        p = store.find("manual")
        self.assertEqual(p["tasks"], [])
        self.assertEqual(p["status"], "active")

    def test_corrupt_file_is_skipped(self):
        (store.projects_dir() / "bad.json").write_text("{nope", encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(store.load_all(), [])


class CliTests(TempHome):
    def test_save_with_flags_updates_everything(self):
        self.run_cli("new", "Demo", "--next", "start")
        self.run_cli("save", "Demo", "-s", "built parser", "-n", "write tests",
                     "--state", "parser done", "-d", "Use JSON | easy to hand-edit",
                     "-t", "task A", "-t", "task B", "-b", "need API key", "-i", "AI brief")
        self.run_cli("save", "Demo", "--done", "1", "--unblock", "1", "--status", "paused")
        p = store.find("demo")
        self.assertEqual(p["next_action"], "write tests")
        self.assertEqual(p["last_state"], "parser done")
        self.assertEqual(p["decisions"][0], {**p["decisions"][0], "text": "Use JSON", "why": "easy to hand-edit"})
        self.assertEqual([t["done"] for t in p["tasks"]], [True, False])
        self.assertEqual(p["blockers"], [])
        self.assertEqual(p["status"], "paused")
        self.assertEqual(len(p["sessions"]), 1)

        out = self.run_cli("show", "demo")
        self.assertIn("NEXT ACTION: write tests", out)
        self.assertIn("2. task B", out)  # numbering matches the full task list
        self.assertNotIn("task A", out)
        self.assertIn("why: easy to hand-edit", out)

    def test_interactive_save(self):
        self.run_cli("new", "Demo")
        # summary, state, tasks..., decisions..., blockers..., ideas..., links..., next, status
        answers = iter(["did stuff", "half way", "new task", "", "Pick X | faster", "",
                        "", "", "", "do Y next", "blocked"])
        with mock.patch("builtins.input", lambda *_: next(answers)), \
             mock.patch("sys.stdin.isatty", return_value=True):
            self.run_cli("save", "Demo")
        p = store.find("demo")
        self.assertEqual(p["sessions"][-1]["summary"], "did stuff")
        self.assertEqual(p["tasks"][0]["text"], "new task")
        self.assertEqual(p["decisions"][0]["why"], "faster")
        self.assertEqual(p["next_action"], "do Y next")
        self.assertEqual(p["status"], "blocked")

    def test_duplicate_new_rejected(self):
        self.run_cli("new", "Demo")
        with self.assertRaises(SystemExit):
            self.run_cli("new", "demo")

    def test_link_replace_and_folder(self):
        self.run_cli("new", "Demo")
        self.run_cli("link", "Demo", "ChatGPT plan", "https://chatgpt.com/c/1")
        self.run_cli("link", "Demo", "ChatGPT plan", "https://chatgpt.com/c/2")
        self.run_cli("link", "Demo", "folder", str(self.tmp))
        p = store.find("demo")
        self.assertEqual(p["links"], [{"label": "ChatGPT plan", "url": "https://chatgpt.com/c/2"}])
        self.assertEqual(p["folder"], str(self.tmp.resolve()))

    def test_list_suggests_resume(self):
        self.run_cli("new", "Old")
        self.run_cli("new", "Untouched")
        self.run_cli("new", "Fresh")
        self.run_cli("save", "Fresh", "-s", "worked", "-n", "ship it")
        old = store.find("old")
        old["sessions"].append({"date": "2020-01-01 10:00:00", "summary": "x", "next": ""})
        store.save(old)  # editing the file later must not make Old look recent
        out = self.run_cli("list")
        self.assertIn("Resume: Fresh -> ship it", out)
        self.assertRegex(out, r"Untouched\s+active\s+never")


class SourceTests(TempHome):
    def test_claude_sessions_parsing(self):
        folder = r"D:\Work\My App"
        d = self.tmp / "claude" / "D--Work-My-App"
        d.mkdir(parents=True)
        lines = [
            {"type": "user", "message": {"content": "hi with \"title\" inside"}},
            {"type": "ai-title", "aiTitle": "Auto title"},
            {"type": "custom-title", "customTitle": "Auth refactor"},
            {"type": "last-prompt", "lastPrompt": "finish the login form"},
        ]
        (d / "abc-123.jsonl").write_text("\n".join(map(json.dumps, lines)) + "\nnot json\n", encoding="utf-8")
        s = sources.claude_sessions(folder)
        self.assertEqual(len(s), 1)
        self.assertEqual(s[0]["id"], "abc-123")
        self.assertEqual(s[0]["title"], "Auth refactor")
        self.assertEqual(s[0]["last_prompt"], "finish the login form")

    def test_chat_date_comes_from_messages_not_file_time(self):
        # Copying/syncing a folder resets file times; old chats must not look new.
        d = self.tmp / "claude" / "D--Work-App"
        d.mkdir(parents=True)
        for name, ts in (("old", "2026-09-01T10:00:00.000Z"), ("new", "2026-10-01T10:00:00.000Z")):
            (d / f"{name}.jsonl").write_text(json.dumps({"type": "user", "timestamp": ts}) + "\n",
                                             encoding="utf-8")
        os.utime(d / "old.jsonl")  # touched last, but its messages are older
        s = sources.claude_sessions(r"D:\Work\App")
        self.assertEqual([x["id"] for x in s], ["new", "old"])
        self.assertEqual(s[0]["last_active"].date().isoformat(), "2026-10-01")

    def test_claude_sessions_missing_dir(self):
        self.assertEqual(sources.claude_sessions(r"C:\nowhere"), [])
        self.assertEqual(sources.claude_sessions(""), [])

    def test_git_info(self):
        repo = self.tmp / "repo"
        repo.mkdir()
        g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
        g("init", "-q")
        g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "first")
        (repo / "x.txt").write_text("x")
        info = sources.git_info(str(repo))
        self.assertEqual(info["last_commit_msg"], "first")
        self.assertEqual(info["uncommitted"], 1)
        self.assertIsNone(sources.git_info(str(self.tmp)))

    def test_git_runs_without_console_window(self):
        # Regression: under pythonw each git call flashed a terminal window.
        (self.tmp / ".git").mkdir()
        done = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with mock.patch("threadback.sources.subprocess.run", return_value=done) as run:
            sources.git_info(str(self.tmp))
        self.assertTrue(run.call_args_list)
        for call in run.call_args_list:
            self.assertEqual(call.kwargs.get("creationflags"), sources.NO_WINDOW)
        if os.name == "nt":
            self.assertNotEqual(sources.NO_WINDOW, 0)

    def test_suggest_resume_prefers_recent_active(self):
        now = datetime.now()
        mk = lambda name, status, hrs: ({"name": name, "status": status},
                                        {"last_activity": now - timedelta(hours=hrs)})
        pick = sources.suggest_resume([mk("a", "active", 5), mk("b", "blocked", 1),
                                       mk("c", "done", 0), mk("d", "active", 2)])
        self.assertEqual(pick[0]["name"], "d")
        self.assertIsNone(sources.suggest_resume([mk("c", "done", 0)]))

    def test_ago(self):
        self.assertEqual(sources.ago(None), "never")
        self.assertEqual(sources.ago(datetime.now() - timedelta(days=3, hours=1)), "3 days ago")
        self.assertEqual(sources.ago(datetime.now() - timedelta(hours=1)), "1 hour ago")


class DashboardTests(TempHome):
    def test_render_escapes_and_includes_context(self):
        p = store.new_project("<script>x</script>")
        p["next_action"] = "fix <b>bug</b>"
        p["tasks"].append({"text": "open task", "done": False})
        p["tasks"].append({"text": "closed task", "done": True})
        p["links"].append({"label": "Claude chat", "url": "https://claude.ai/chat/1"})
        p["sessions"].append({"date": store.now(), "summary": "worked", "next": ""})
        store.save(p)
        html = dashboard.write().read_text(encoding="utf-8")
        self.assertNotIn("<script>x", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("Resume this", html)
        self.assertIn("open task", html)
        self.assertNotIn("closed task", html)
        self.assertIn('href="https://claude.ai/chat/1"', html)

    def test_empty_dashboard(self):
        self.assertIn("No projects yet", dashboard.render([]))


class PluginTests(TempHome):
    def make_project(self, name="Proj", next_action="add login page"):
        folder = self.tmp / name.lower()
        (folder / "src").mkdir(parents=True)
        p = store.new_project(name, folder=str(folder))
        p["next_action"] = next_action
        p["tasks"].append({"text": "write docs", "done": False})
        p["decisions"].append({"date": store.now(), "text": "Use SQLite", "why": "no server"})
        store.save(p)
        return folder

    def hook(self, event, payload):
        buf = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(json.dumps(payload))), redirect_stdout(buf):
            cli.main(["hook", event])
        return buf.getvalue()

    def write_chat(self, folder, sid, messages):
        """Transcript with (minutes_from_now, role, text) records, timestamps in UTC like Claude Code."""
        from datetime import timezone
        d = sources.claude_dir_for(str(folder))
        d.mkdir(parents=True, exist_ok=True)
        recs = []
        for minutes, role, text in messages:
            ts = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")
            content = text if role == "user" else [{"type": "text", "text": text}]
            recs.append({"type": role, "timestamp": ts, "message": {"content": content}})
        (d / f"{sid}.jsonl").write_text("\n".join(map(json.dumps, recs)) + "\n", encoding="utf-8")

    def test_chat_ending_without_save_is_auto_captured(self):
        folder = self.make_project()
        self.write_chat(folder, "s1", [(-5, "user", "fix the date picker"),
                                       (-4, "assistant", "Fixed it in picker.ts")])
        self.hook("session-end", {"session_id": "s1", "cwd": str(folder)})
        # Session end only notes the chat (it has 1.5 s); opening the project captures it.
        p = store.find("proj")
        self.assertEqual(p.get("auto_saves", []), [])
        self.assertEqual([c["session_id"] for c in p["pending_captures"]], ["s1"])
        self.assertIn("ended without saving (1", self.run_cli("show", "proj"))
        p = store.find("proj")
        self.assertEqual(p["next_action"], "add login page")  # the user's plan is untouched
        self.assertEqual(p["auto_saves"][0]["asked"], ["fix the date picker"])
        self.assertEqual(p["auto_saves"][0]["last_answer"], "Fixed it in picker.ts")
        self.assertEqual(p["pending_captures"], [])
        ctx = json.loads(self.hook("session-start", {"session_id": "s2", "cwd": str(folder)}))
        self.assertIn("Last asked: fix the date picker", ctx["hookSpecificOutput"]["additionalContext"])
        # A real save covers them.
        self.run_cli("save", "proj", "-s", "fixed date picker")
        self.assertEqual(store.find("proj")["auto_saves"], [])

    def test_session_start_lists_every_open_task(self):
        folder = self.make_project()
        p = store.find("proj")
        p["tasks"] += [{"text": f"task {n}", "done": False} for n in range(2, 16)]
        p["tasks"][2]["done"] = True
        store.save(p)
        ctx = json.loads(self.hook("session-start", {"session_id": "s1", "cwd": str(folder)}))
        text = ctx["hookSpecificOutput"]["additionalContext"]
        self.assertIn("15. task 15", text)  # nothing past the tenth is dropped
        self.assertNotIn("task 3\n", text)  # done tasks stay out
        self.assertIn("Use SQLite", text)  # decisions still fit after the tasks

    def test_only_messages_after_the_last_save_are_captured(self):
        folder = self.make_project()
        self.run_cli("save", "proj", "-s", "did the first part")
        self.write_chat(folder, "s1", [(-10, "user", "first part"), (-9, "assistant", "done")])
        self.hook("session-end", {"session_id": "s1", "cwd": str(folder)})
        self.hook("session-start", {"session_id": "s2", "cwd": str(folder)})  # captures it
        self.assertEqual(store.find("proj").get("auto_saves", []), [])  # saved, nothing new
        self.write_chat(folder, "s1", [(-10, "user", "first part"), (1, "user", "one more thing")])
        self.hook("session-end", {"session_id": "s1", "cwd": str(folder)})
        self.hook("session-start", {"session_id": "s3", "cwd": str(folder)})
        self.assertEqual(store.find("proj")["auto_saves"][0]["asked"], ["one more thing"])

    def test_find_by_folder_matches_subfolders_deepest_first(self):
        outer = self.make_project("Outer")
        inner = store.new_project("Inner", folder=str(outer / "src"))
        store.save(inner)
        self.assertEqual(store.find_by_folder(str(outer))["name"], "Outer")
        self.assertEqual(store.find_by_folder(str(outer / "src" / "deep"))["name"], "Inner")
        self.assertIsNone(store.find_by_folder(str(self.tmp)))

    def test_session_start_gives_context_and_records_session(self):
        folder = self.make_project()
        out = json.loads(self.hook("session-start", {"session_id": "s1", "cwd": str(folder / "src")}))
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("Next step the user planned: add login page", ctx)
        self.assertIn("1. write docs", ctx)
        self.assertIn("Use SQLite (why: no server)", ctx)
        self.hook("session-end", {"session_id": "s1", "cwd": str(folder)})
        rec = store.find("proj")["claude_sessions"]
        self.assertEqual([r["id"] for r in rec], ["s1"])
        self.assertTrue(rec[0]["ended"])

    def test_session_start_in_untracked_folder_gives_hint(self):
        out = json.loads(self.hook("session-start", {"session_id": "s1", "cwd": str(self.tmp)}))
        self.assertIn("not a tracked project", out["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(store.load_all(), [])

    def test_hook_never_fails_on_bad_input(self):
        buf = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO("{not json")), redirect_stdout(buf):
            cli.main(["hook", "session-start"])  # must not raise or exit non-zero
        self.assertEqual(buf.getvalue(), "")
        self.assertIn("JSONDecodeError", (store.home() / "hook-errors.log").read_text(encoding="utf-8"))

    def test_recorded_sessions_survive_broken_transcript_scan(self):
        folder = self.make_project()
        self.hook("session-start", {"session_id": "abc", "cwd": str(folder)})
        with mock.patch("threadback.sources.claude_sessions", side_effect=RuntimeError("format changed")):
            ctx = sources.context(store.find("proj"))
        self.assertEqual([s["id"] for s in ctx["claude"]], ["abc"])
        self.assertIsNotNone(ctx["last_activity"])

    def test_save_without_name_uses_or_registers_current_folder(self):
        folder = self.make_project()
        new_folder = self.tmp / "Brand New"
        new_folder.mkdir()
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(folder / "src")
        self.run_cli("save", "-s", "did a thing", "-n", "next thing")
        self.assertEqual(store.find("proj")["next_action"], "next thing")
        os.chdir(new_folder)
        out = self.run_cli("save", "-n", "set up repo")
        self.assertIn("Registered new project 'Brand New'", out)
        self.assertEqual(store.find("brand-new")["next_action"], "set up repo")
        self.assertIn("NEXT ACTION: set up repo", self.run_cli("show"))

    def test_register_refuses_home(self):
        with self.assertRaises(ValueError):
            store.register_folder(str(Path.home()))


@unittest.skipUnless(os.name == "nt", "the welcome window and startup are a Windows-only extra")
class StartupTests(TempHome):
    def test_install_and_remove(self):
        (self.tmp / "desktop").mkdir()
        script, shortcut = startup.install()
        text = script.read_bytes().decode("utf-8")  # line endings exactly as written
        self.assertIn("-m threadback welcome", text)
        self.assertNotIn("\r\r\n", text)
        self.assertIn("THREADBACK_HOME", text)
        self.assertTrue(shortcut.exists())
        self.assertTrue(startup.remove())
        self.assertFalse(script.exists())
        self.assertFalse(shortcut.exists())
        self.assertFalse(startup.remove())


class PopupTests(TempHome):
    def test_summary_picks_recent_and_lists_others(self):
        from threadback import popup
        for name in ("A", "B", "C"):
            store.save(store.new_project(name))
        b = store.find("b")
        b["sessions"].append({"date": store.now(), "summary": "x", "next": ""})
        b["next_action"] = "do it"
        store.save(b)
        s = popup.summary()
        self.assertEqual(s["pick"][0]["name"], "B")
        self.assertEqual(sorted(p["name"] for p, _ in s["others"]), ["A", "C"])

    def test_quick_save_records_session_and_next_step(self):
        from threadback import popup
        store.save(store.new_project("Demo"))
        popup.quick_save("demo", "fixed login bug", "write tests for login")
        popup.quick_save("demo", "", "")  # empty save changes nothing
        p = store.find("demo")
        self.assertEqual(p["next_action"], "write tests for login")
        self.assertEqual(len(p["sessions"]), 1)
        self.assertEqual(p["sessions"][0]["summary"], "fixed login bug")
        # A quick save makes the project the one to resume.
        self.assertEqual(popup.summary()["pick"][0]["name"], "Demo")

    def test_quick_save_without_next_step(self):
        from threadback import popup
        store.save(store.new_project("Demo"))
        popup.quick_save("demo", "looked around the code", "")
        p = store.find("demo")
        self.assertEqual(p["next_action"], "")
        self.assertEqual(p["sessions"][0]["summary"], "looked around the code")

    def test_window_builds(self):
        import tkinter
        from threadback import popup
        store.save(store.new_project("Demo"))
        popup.quick_save("demo", "x", "next")
        store.save(store.new_project("Other"))
        try:
            tkinter.Tk().destroy()
        except tkinter.TclError:
            self.skipTest("no display")
        built = {}

        def fake_mainloop(root):
            built["labels"] = [w.cget("text") for w in root.winfo_children()[0].winfo_children()
                               if isinstance(w, tkinter.Label)]
            root.destroy()

        with mock.patch.object(tkinter.Tk, "mainloop", fake_mainloop):
            popup.show()
        self.assertIn("RESUME THIS", built["labels"])
        self.assertIn("Next: next", built["labels"])


class DigestTests(TempHome):
    def make_folder(self):
        """A git repo with a README and one earlier Claude chat full of noise."""
        folder = self.tmp / "old app"
        folder.mkdir()
        (folder / "README.md").write_text("# Old App\n\nTracks garden watering.\n", encoding="utf-8")
        g = lambda *a: subprocess.run(["git", "-C", str(folder), *a], check=True, capture_output=True)
        g("init", "-q")
        g("add", ".")
        g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "Add watering schedule")
        d = sources.claude_dir_for(str(folder))
        d.mkdir(parents=True)
        lines = [
            {"type": "custom-title", "customTitle": "Reminder emails"},
            {"type": "user", "message": {"content": "add email reminders before watering"}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "x" * 5000}]}},
            {"type": "user", "isMeta": True, "message": {"content": "meta noise"}},
            {"type": "user", "message": {"content": "<command-name>/clear</command-name>"}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read"}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Reminders work; SMTP not set up yet."}]}},
        ]
        chat = d / "chat-1.jsonl"
        chat.write_text("\n".join(map(json.dumps, lines)) + "\n", encoding="utf-8")
        t = (datetime.now() + timedelta(seconds=30)).timestamp()  # chat after the commit
        os.utime(chat, (t, t))
        return folder

    def test_digest_keeps_what_matters_and_drops_noise(self):
        from threadback import digest
        folder = self.make_folder()
        (folder / "notes.txt").write_text("wip", encoding="utf-8")
        text = digest.build(str(folder))
        self.assertIn("Uncommitted changes (1): notes.txt", text)
        self.assertIn("Tracks garden watering.", text)
        self.assertIn("Add watering schedule  [README.md]", text)
        self.assertIn("Claude chat: Reminder emails", text)
        self.assertIn("- add email reminders before watering", text)
        self.assertIn("Claude's last answer: Reminders work; SMTP not set up yet.", text)
        for noise in ("xxxx", "meta noise", "/clear"):
            self.assertNotIn(noise, text)

    def test_digest_since_last_save_and_size_cap(self):
        from threadback import digest
        folder = str(self.make_folder())
        later = datetime.now() + timedelta(minutes=5)
        self.assertIn("No commits or Claude Code chats found since then.", digest.build(folder, later))
        self.assertLessEqual(len(digest.build(folder, max_chars=200)), 200)

    def test_digest_command_and_catch_up_at_session_start(self):
        folder = self.make_folder()
        store.save(store.new_project("Old App", folder=str(folder)))
        self.assertIn("Reminder emails", self.run_cli("digest", "old app"))
        self.assertIn("Nothing saved yet. Seen in the folder: last chat: Reminder emails",
                      self.run_cli("show", "old app"))
        buf = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(json.dumps({"cwd": str(folder)}))), redirect_stdout(buf):
            cli.main(["hook", "session-start"])
        ctx = json.loads(buf.getvalue())["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Nothing has been saved for this project yet", ctx)
        self.assertIn("add email reminders before watering", ctx)
        # Once something is saved, the digest no longer rides along in every chat.
        self.run_cli("save", "old app", "-s", "caught up", "-n", "set up SMTP")
        buf = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(json.dumps({"cwd": str(folder)}))), redirect_stdout(buf):
            cli.main(["hook", "session-start"])
        self.assertNotIn("Automatic digest", buf.getvalue())


# Fake keys are built from pieces so the repo never holds a key-shaped string
# (GitHub push protection would block it, and it would be a bad example).
FAKE = {
    "anthropic": "sk-ant-" + "api03-" + "A1b2" * 10,
    "openai": "sk-" + "proj-" + "Z9y8" * 8,
    "github": "ghp" + "_" + "Q" * 36,
    "aws": "AK" + "IA" + "ABCDEFGHIJKLMNOP",
    "google": "AI" + "za" + "x" * 35,
    "slack": "xo" + "xb-" + "1234567890-abcdef",
    "jwt": "ey" + "J" + "a" * 20 + ".ey" + "J" + "b" * 20 + "." + "c" * 20,
}


class RedactTests(TempHome):
    def test_known_key_formats_are_masked(self):
        from threadback.redact import MASK, redact
        for name, key in FAKE.items():
            with self.subTest(name):
                out = redact(f"use this key {key} for now")
                self.assertNotIn(key, out)
                self.assertEqual(out, f"use this key {MASK} for now")

    def test_labelled_secrets_keep_the_label(self):
        from threadback.redact import redact
        cases = {
            "password=hunter2xyz": "password=[REDACTED]",
            'DB_PASSWORD: "s3cr3t-pw"': "DB_PASSWORD: [REDACTED]",
            "api_key = 12345abcdef": "api_key = [REDACTED]",
            "Authorization: Bearer abcdefghijklmnop1234": "Authorization: Bearer [REDACTED]",
            "postgres://admin:topsecret@db.local/app": "postgres://admin:[REDACTED]@db.local/app",
            "https://x.com/cb?token=abcdef123456&x=1": "https://x.com/cb?token=[REDACTED]&x=1",
        }
        for text, want in cases.items():
            with self.subTest(text):
                self.assertEqual(redact(text), want)

    def test_ordinary_text_is_left_alone(self):
        from threadback.redact import redact
        for text in ["Fixed the password reset flow", "max_tokens=4096", "author: Sam",
                     "Use OAuth for login", "Bumped version to 0.4.1", "secret sauce: none",
                     "D:/Projects/my-app", "Tokens: 1200 used"]:
            with self.subTest(text):
                self.assertEqual(redact(text), text)

    def test_saved_context_never_holds_a_key(self):
        key = FAKE["github"]
        self.run_cli("new", "Vault App")
        self.run_cli("save", "Vault App", "-s", f"Set token {key}", "-n", "Rotate the key",
                     "-d", f"Use env vars | {key} was pasted in chat", "-t", f"remove {key}")
        on_disk = store.path_for("vault-app").read_text(encoding="utf-8")
        self.assertNotIn(key, on_disk)
        self.assertIn("Set token [REDACTED]", on_disk)
        self.assertNotIn(key, self.run_cli("show", "Vault App"))

    def test_digest_never_shows_a_key(self):
        from threadback import digest
        folder = self.tmp / "keys app"
        folder.mkdir()
        key = FAKE["anthropic"]
        (folder / "README.md").write_text(f"# Keys\n\nANTHROPIC_API_KEY={key}\n", encoding="utf-8")
        g = lambda *a: subprocess.run(["git", "-C", str(folder), *a], check=True, capture_output=True)
        g("init", "-q")
        g("add", ".")
        g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", f"Add key {key}")
        text = digest.build(str(folder))
        self.assertNotIn(key, text)
        self.assertIn("Add key [REDACTED]", text)

    def test_branch_and_commit_are_masked_in_what_claude_gets(self):
        from threadback import hooks
        folder = self.tmp / "branchy"
        folder.mkdir()
        key = FAKE["github"]
        g = lambda *a: subprocess.run(["git", "-C", str(folder), *a], check=True, capture_output=True)
        g("init", "-q")
        g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", f"token {key}")
        g("checkout", "-q", "-b", f"fix-{key}")
        (folder / "x.txt").write_text("dirty")  # uncommitted, so the branch line is shown
        p = store.new_project("Branchy", folder=str(folder))
        p["next_action"] = "go"
        store.save(p)
        out = json.loads(self.run_hook(hooks, str(folder)))["hookSpecificOutput"]["additionalContext"]
        self.assertIn("uncommitted change", out)
        self.assertNotIn(key, out)
        self.assertNotIn(key, self.run_cli("show", "branchy"))

    def run_hook(self, hooks, cwd):
        buf = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(json.dumps({"session_id": "s", "cwd": cwd}))), \
                redirect_stdout(buf):
            hooks.run("session-start")
        return buf.getvalue()


class NoPythonTests(unittest.TestCase):
    """scripts/tb with no usable Python on PATH: say so instead of failing silently."""

    def run_tb(self, *args):
        import shutil
        bash = shutil.which("bash")
        # Windows' System32 bash is WSL, a different machine; only test a real bash.
        if not bash or "system32" in bash.lower():
            self.skipTest("needs bash (Git Bash on Windows)")
        tb = Path(__file__).resolve().parent.parent / "scripts" / "tb"
        if os.name == "nt":
            path = str(Path(bash).parent)  # Git Bash's own folder has no Python
        else:  # /usr/bin holds python3 on Mac/Linux, so offer only what scripts/tb needs
            bin_dir = Path(tempfile.mkdtemp())
            for tool in ("bash", "dirname"):
                (bin_dir / tool).symlink_to(shutil.which(tool))
            path = str(bin_dir)
        env = {**os.environ, "PATH": path}
        return subprocess.run([bash, str(tb), *args], input="{}", capture_output=True,
                              text=True, env=env, timeout=30)

    def test_session_start_tells_the_user(self):
        ran = self.run_tb("hook", "session-start")
        self.assertEqual(ran.returncode, 0)  # never breaks the session
        out = json.loads(ran.stdout)
        self.assertIn("needs Python 3.10", out["systemMessage"])
        self.assertIn("needs Python 3.10", out["hookSpecificOutput"]["additionalContext"])

    def test_session_end_stays_quiet_and_commands_explain(self):
        ended = self.run_tb("hook", "session-end")
        self.assertEqual((ended.returncode, ended.stdout), (0, ""))
        shown = self.run_tb("show")
        self.assertEqual(shown.returncode, 1)
        self.assertIn("python.org", shown.stderr)


class TaskEditTests(TempHome):
    """`tb task`, which the panel's buttons call."""

    def tasks(self):
        p = store.find("Shop")
        return [(t["text"], t["done"]) for t in p["tasks"]], p["next_action"]

    def setUp(self):
        super().setUp()
        self.run_cli("new", "Shop")
        self.run_cli("save", "Shop", "-n", "Ship v1", "-t", "A", "-t", "B", "-t", "C")

    def test_add_done_undo_remove(self):
        self.run_cli("task", "add", "D", "-p", "Shop")
        self.run_cli("task", "done", "2", "-p", "Shop")
        self.assertEqual(self.tasks()[0], [("A", False), ("B", True), ("C", False), ("D", False)])
        self.assertTrue(store.find("Shop")["tasks"][1]["done_on"])
        self.run_cli("task", "undo", "2", "-p", "Shop")
        self.run_cli("task", "remove", "1", "-p", "Shop")
        self.assertEqual(self.tasks()[0], [("B", False), ("C", False), ("D", False)])

    def test_top_and_next(self):
        self.run_cli("task", "top", "3", "-p", "Shop")
        self.assertEqual([t for t, _ in self.tasks()[0]], ["C", "A", "B"])
        self.run_cli("task", "next", "2", "-p", "Shop")  # A becomes the next step
        tasks, nxt = self.tasks()
        self.assertEqual(nxt, "A")
        self.assertEqual([t for t, _ in tasks], ["Ship v1", "C", "B"])  # old next step kept

    def test_bad_input_is_refused(self):
        with self.assertRaises(SystemExit):
            self.run_cli("task", "done", "9", "-p", "Shop")
        with self.assertRaises(SystemExit):
            self.run_cli("task", "add", "  ", "-p", "Shop")
        self.assertEqual(len(self.tasks()[0]), 3)

    def test_session_start_tells_the_panel_where_tb_is(self):
        from threadback import hooks
        hooks.note_tb_path()
        tb = Path((store.home() / "tb-path").read_text(encoding="utf-8"))
        self.assertEqual(tb.name, "tb.py")
        self.assertTrue(tb.exists())


if __name__ == "__main__":
    unittest.main()


class SafeCommandTests(TempHome):
    """What the slash commands run: chat text and project folders never become shell syntax."""

    def setUp(self):
        super().setUp()
        self.run_cli("new", "Shop")

    def save_json(self, data):
        with mock.patch("sys.stdin", io.StringIO(json.dumps(data))):
            return self.run_cli("save", "Shop", "--json")

    def test_save_json_keeps_shell_characters_as_text(self):
        risky = 'Fix "quotes", $(rm -rf ~) and `whoami`'
        self.save_json({"summary": risky, "next": "Ship $HOME v1",
                        "decisions": [{"text": "Use SQLite", "why": "no server"}, "Keep JSON | readable"],
                        "tasks": ["A", "B"], "ideas": ["dark mode"], "status": "active"})
        p = store.find("Shop")
        self.assertEqual(p["sessions"][-1]["summary"], risky)
        self.assertEqual(p["next_action"], "Ship $HOME v1")
        self.assertEqual([(d["text"], d["why"]) for d in p["decisions"]],
                         [("Use SQLite", "no server"), ("Keep JSON", "readable")])
        self.save_json({"done": [2], "tasks": ["C"]})
        self.assertEqual([(t["text"], t["done"]) for t in store.find("Shop")["tasks"]],
                         [("A", False), ("B", True), ("C", False)])

    def test_save_json_rejects_bad_input(self):
        for bad in ('not json', '["a list"]', '{"sumary": "typo"}', '{"status": "bogus"}'):
            with self.subTest(bad), mock.patch("sys.stdin", io.StringIO(bad)), \
                    self.assertRaises(SystemExit):
                self.run_cli("save", "Shop", "--json")

    def test_list_slugs_and_safely_quoted_resume_command(self):
        import shlex
        folder = self.tmp / "it's $(whoami) app"
        folder.mkdir()
        self.run_cli("link", "Shop", "folder", str(folder))
        self.assertIn("[shop]", self.run_cli("list", "--slugs"))
        self.assertNotIn("[shop]", self.run_cli("list"))
        line = self.run_cli("open", "shop", "claude").strip()
        self.assertEqual(shlex.split(line), ["cd", str(folder.resolve()), "&&", "claude"])


class LockTests(TempHome):
    def test_parallel_task_edits_lose_nothing(self):
        """The panel's buttons and the hooks can write at the same moment: every edit must land."""
        self.run_cli("new", "Shop")
        tb = Path(__file__).resolve().parent.parent / "scripts" / "tb.py"
        procs = [subprocess.Popen([sys.executable, str(tb), "task", "add", f"task {n}", "-p", "shop"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                 for n in range(12)]
        errors = [p.communicate(timeout=60)[1] for p in procs]
        self.assertEqual([p.returncode for p in procs], [0] * 12, errors)
        texts = sorted(t["text"] for t in store.find("shop")["tasks"])
        self.assertEqual(texts, sorted(f"task {n}" for n in range(12)))
        leftovers = [f.name for f in store.projects_dir().iterdir() if f.suffix == ".tmp"]
        self.assertEqual(leftovers, [])
        self.assertFalse((store.home() / ".lock").exists())

    def test_stale_lock_is_taken_over(self):
        lock = store.home() / ".lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("")
        old = datetime.now().timestamp() - 60
        os.utime(lock, (old, old))
        with store.lock(timeout=1):
            pass
        self.assertFalse(lock.exists())


class SecondReviewTests(TempHome):
    """Findings of the second pre-release review."""

    def test_lock_that_cannot_be_taken_never_hangs(self):
        import time
        store.home().mkdir(parents=True, exist_ok=True)
        (store.home() / ".lock").mkdir()  # a folder in the way: can't be created or removed
        old = datetime.now().timestamp() - 60
        os.utime(store.home() / ".lock", (old, old))
        start = time.monotonic()
        with mock.patch("sys.stderr", io.StringIO()) as err, store.lock(timeout=0.5):
            pass
        self.assertLess(time.monotonic() - start, 3)
        self.assertIn("going ahead without it", err.getvalue())

    def test_hook_stdout_is_one_json_object_despite_a_corrupt_project(self):
        folder = self.tmp / "app"
        folder.mkdir()
        p = store.new_project("App", folder=str(folder))
        p["next_action"] = "go"
        store.save(p)
        (store.projects_dir() / "broken.json").write_text("{ not json", encoding="utf-8")
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(json.dumps({"session_id": "s", "cwd": str(folder)}))), \
                redirect_stdout(out), mock.patch("sys.stderr", io.StringIO()) as err:
            from threadback import hooks
            hooks.run("session-start")
        self.assertIn("go", json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"])
        self.assertIn("broken.json", err.getvalue())

    def test_a_failed_capture_is_retried_then_given_up(self):
        from threadback import hooks
        p = store.new_project("App")
        p["pending_captures"] = [{"session_id": "s1", "cwd": "", "transcript_path": "x"}]
        with mock.patch.object(hooks, "auto_capture", side_effect=OSError("locked")):
            for tries in (1, 2):
                hooks.capture_pending(p)
                self.assertEqual([c["tries"] for c in p["pending_captures"]], [tries])
            hooks.capture_pending(p)
        self.assertEqual(p["pending_captures"], [])  # third failure: dropped
        log = (store.home() / "hook-errors.log").read_text(encoding="utf-8")
        self.assertEqual(log.count("locked"), 3)

    def test_save_json_checks_types(self):
        self.run_cli("new", "Shop")
        for bad in ('{"summary": 5}', '{"next": ["a"]}', '{"done": ["2"]}', '{"done": [true]}',
                    '{"done": [{"n": 1}]}', '{"tasks": [3]}', '{"decisions": [{"text": 1}]}',
                    '{"decisions": [{"text": "a", "extra": "b"}]}'):
            with self.subTest(bad), mock.patch("sys.stdin", io.StringIO(bad)), \
                    self.assertRaises(SystemExit) as caught:
                self.run_cli("save", "Shop", "--json")
            self.assertIn("--json", str(caught.exception.code))

    def test_parallel_first_saves_make_one_project(self):
        folder = self.tmp / "fresh app"
        folder.mkdir()
        tb = Path(__file__).resolve().parent.parent / "scripts" / "tb.py"
        procs = [subprocess.Popen([sys.executable, str(tb), "save", "-t", f"task {n}"], cwd=folder,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE) for n in range(6)]
        errors = [p.communicate(timeout=60)[1] for p in procs]
        self.assertEqual([p.returncode for p in procs], [0] * 6, errors)
        projects = store.load_all()
        self.assertEqual([p["name"] for p in projects], ["fresh app"])
        self.assertEqual(len(projects[0]["tasks"]), 6)
