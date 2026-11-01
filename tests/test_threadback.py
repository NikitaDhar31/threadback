import io
import json
import os
import subprocess
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


class StartupTests(TempHome):
    def test_install_and_remove(self):
        (self.tmp / "desktop").mkdir()
        script, shortcut = startup.install()
        text = script.read_text(encoding="utf-8", newline="")
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


if __name__ == "__main__":
    unittest.main()
