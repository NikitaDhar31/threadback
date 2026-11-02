---
description: List all your projects and their next steps, and pick one to resume (ThreadBack)
argument-hint: "[optional: a project name to open]"
---

Show the user every project ThreadBack tracks, so they can pick what to resume.

Project the user named (may be empty): $ARGUMENTS

1. If they named a project, skip to step 3 with that name. Otherwise run:

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/tb" list
   ```

2. Reply with:
   - The suggested project from the `Resume:` line and its next step, first and in bold.
   - Then the other projects as a short list: name, when last active, next step.
     Copy each next step as saved, don't reword it. Leave out the status unless it isn't "active".

   If there are no projects yet, say so in one line and mention that `/threadback-save`
   in a project folder starts tracking it. End by asking which project to resume.

3. When the user picks a project (or named one), run both:

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/tb" show "NAME"
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/tb" open "NAME" claude
   ```

   Tell them that project's next step and where they left off, in at most 6 lines. Then give
   the command from the second output, which reopens their latest Claude Code chat there, in
   its own `bash` code block. If there is no earlier chat, give `cd "<folder>" && claude`
   instead, using the folder from the `show` output. Don't start work on that project here:
   this chat is in a different folder.
