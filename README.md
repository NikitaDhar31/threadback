# ThreadBack

Personal work-context recovery for people juggling several AI-assisted projects.
When you come back after days away, ThreadBack tells you **what you were doing, why,
what's unfinished, and where the relevant conversation is**.

It is a resumption dashboard, not another coding assistant. Pure Python standard
library, no database, no network calls, no credentials.

## Quick start

```bash
git clone https://github.com/NikitaDhar31/threadback.git
cd threadback
py -m threadback new "My Project" --folder "C:\path\to\my-project" --next "first thing to do"
py -m threadback link "My Project" "ChatGPT: API design" https://chatgpt.com/c/...
py -m threadback                      # list projects + which one to resume
py -m threadback show "my project"    # full context brief
py -m threadback save "my project"    # end-of-session prompts (Enter skips any)
py -m threadback welcome              # welcome window with a Save button per project
py -m threadback dashboard            # HTML dashboard in your browser
py -m threadback startup install      # welcome window at Windows login + desktop icon
```

Use `python3` instead of `py` outside Windows. Optional: `py -m pip install -e .` gives
you a plain `threadback` command.

Project names match loosely (`show my` finds "My Project" if unambiguous).

## Commands

| Command | What it does |
|---|---|
| `new NAME [--folder P] [--next T] [--link "label \| url"]` | Create a project |
| `list [--all]` (default) | Projects by last activity, with a "Resume:" pick |
| `show NAME` | Next action, last state, blockers, open tasks, decisions + why, ideas, git, Claude Code sessions, links |
| `save NAME` | Interactive session summary. Non-interactive flags: `-s` summary, `--state`, `-n` next, `-d "decision \| why"`, `-t` task, `--done N`, `-b` blocker, `--unblock N`, `-i` idea, `--status`, `--link` |
| `digest [NAME] [--all] [--chats N]` | Short summary of the folder's commits, README and earlier Claude chats since the last save (`--all`: everything recent). Local and free, no AI |
| `link NAME LABEL URL` | Add/replace a link (ChatGPT chat, Claude chat, GitHub repo...). `LABEL=folder` sets the folder |
| `open NAME [LABEL\|folder\|claude]` | Open a link or the folder; `claude` prints the `claude --resume` command |
| `edit NAME` | Open the project's JSON in `$EDITOR` / default app |
| `dashboard [--no-open]` | Regenerate `~/.threadback/dashboard.html` |
| `startup install\|remove\|status` | Manage the login script |

## Claude Code plugin

This repo is also a Claude Code plugin (`.claude-plugin/`, `hooks/`, `commands/`).

- **Session start:** in a registered project folder (or any subfolder), Claude is
  given that project's next step, open tasks, decisions and recent session
  summaries, and the session is recorded. Untracked folders get a one-line hint.
- **Session end:** records when the session ended. If the chat ended without
  `/threadback-save`, it keeps a free trace (your last messages and Claude's last answer,
  no AI). It never changes your next step, and the next real save replaces it.
- **`/threadback`:** shows where you left off.
- **`/threadback-save [note]`:** Claude writes the summary, decisions and next step
  for you. In an untracked folder it registers the folder as a new project.
- **Catching up old projects:** open a new chat in the folder and run `/threadback-save`.
  With nothing to save from the chat itself, Claude reads `tb digest` (a page-sized,
  pre-filtered summary of commits and earlier chats) rather than the raw transcripts,
  so a catch-up costs about as much as a short chat. Until a project has a save, new
  chats there also get a short digest at session start.

Hooks use only the documented hook input (`session_id`, `cwd`), so they keep working
if Claude Code changes its transcript format. Hook errors never block a session; they
go to `~/.threadback/hook-errors.log`.

Install from GitHub:

```bash
claude plugin marketplace add NikitaDhar31/threadback
claude plugin install threadback@threadback
```

Or, to develop it, from a local clone: `claude plugin marketplace add <path-to-your-clone>`.
The hooks need Python 3 (`py` on Windows, `python3` elsewhere) and `bash` (Git Bash on Windows).

Claude Code runs an installed *copy* of the plugin. After changing the code, bump
`version` in `.claude-plugin/plugin.json` (updates are skipped otherwise), then:

```bash
claude plugin marketplace update threadback
claude plugin update threadback@threadback
```

## Where data lives

- `~/.threadback/projects/<slug>.json`: one hand-editable file per project.
  Set `THREADBACK_HOME` to move it (e.g. into a synced folder).
- Nothing is stored in this repository, so personal context never gets committed.

## What is detected automatically (read-only)

- **Git** (if the folder is a repo): branch, last commit, uncommitted changes.
- **Claude Code sessions**: Claude Code keeps transcripts locally under
  `~/.claude/projects/<folder-name>/`. ThreadBack reads the session title and last
  prompt, and gives you the exact `claude --resume <id>` command. This format is
  undocumented, so parsing is defensive. If it changes, that section just disappears.
- **ChatGPT / claude.ai chats**: no supported local source, so you paste conversation
  links with `link` or `save --link`. (ChatGPT's *Settings → Data controls → Export*
  zip could be imported later; it's not in the MVP.)

"Last activity" is the most recent of: saved session, git commit, Claude Code
session. Editing a project's file (`new`, `link`) does not count as activity.

## Startup (Windows)

`startup install` writes `ThreadBack.cmd` into your per-user Startup folder
(`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`) and puts a ThreadBack
icon on your desktop. Both open the welcome window: the project to resume, its next
step, and a **Save** button per project. No admin rights and no registry changes;
`startup remove` deletes both. If you move this folder, run `install` again.

## Tests

```bash
py -m unittest discover -s tests -t .
```

## Possible next steps

- Import ChatGPT's official data export to auto-link conversations by project.
- Optional AI-generated resumption brief from the saved context.
