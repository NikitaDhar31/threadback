---
description: Save this session's progress and next step to ThreadBack
argument-hint: "[optional: your next step or a note]"
---

Save this session's progress to ThreadBack so the user can resume quickly next time.
The point is speed: don't ask questions unless something is genuinely ambiguous.

User's note (may be empty; if present, it wins for the next step): $ARGUMENTS

1. See what is already saved, including task numbers:

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/tb" show
   ```

   If it says the folder isn't a project yet, that's fine: the save below registers it.

2. From **this conversation only**, draft (leave out anything that doesn't apply; never invent):
   - `-s` summary: 1–2 sentences on what was actually done.
   - `--state`: where things stand now (what works, what's half-done).
   - `-n` next step: ONE concrete action to start with next time (file, feature, command).
   - `-d "decision | why"`: real decisions made this session, with the reason (0–3).
   - `-t`: new unfinished tasks. `--done N`: numbers of saved tasks finished this session.
   - `-b`: new blockers. `--unblock N`: saved blockers now resolved.
   - `-i`: ideas discussed but not implemented.

3. Save in a single command from the project folder, double-quoting every value
   (escape any inner double quotes). Example:

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/tb" save -s "..." --state "..." -n "..." -d "... | ..." -t "..."
   ```

4. Reply in at most 5 lines: what was saved, and the next step, word for word.
