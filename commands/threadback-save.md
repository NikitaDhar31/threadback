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
   If it lists chats that ended without saving, include what they did in the summary
   (they were auto-captured; this save replaces them).

   **Catch-up mode:** if this conversation has no real work in it yet (the user opened a chat
   just to save, or asks to catch up on an old project), don't draft from memory. Get a short,
   pre-filtered summary of the folder's commits and earlier Claude chats instead:

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/tb" digest
   ```

   It covers only activity since the last save (add `--all` if nothing useful comes back).
   Draft from the digest, start `-s` with "Catch-up:", and suggest a next step that picks up
   where the most recent chat or commit stopped. Don't open the transcript files themselves:
   they are large and the digest already holds what matters.

2. From **this conversation** (or the digest, in catch-up mode), draft (leave out anything that doesn't apply; never invent):
   - `-s` summary: 1–2 sentences on what was actually done.
   - `--state`: where things stand now (what works, what's half-done).
   - `-n` next step: ONE concrete action to start with next time (file, feature, command).
   - `-d "decision | why"`: real decisions made this session, with the reason (0–3).
   - `-t`: new unfinished tasks. `--done N`: numbers of saved tasks finished this session.
     Before adding a task, check whether the user already said they did it ("done", "it's on");
     if so, don't add it (or mark it `--done` if it was saved earlier).
   - `-b`: new blockers. `--unblock N`: saved blockers now resolved.
   - `-i`: ideas discussed but not implemented.

3. Save in a single command from the project folder, double-quoting every value
   (escape any inner double quotes). Example:

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/tb" save -s "..." --state "..." -n "..." -d "... | ..." -t "..."
   ```

4. Reply in at most 5 lines: what was saved, and the next step, word for word.
