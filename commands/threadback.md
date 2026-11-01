---
description: Show where you left off on this project (ThreadBack)
---

Run this to get the saved context for the project in the current folder:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/tb" show
```

Then tell the user, briefly and in this order:
1. The **next step** they planned (the most important line).
2. Where they left off, and any blockers.
3. Unfinished tasks and the last few decisions, only if there are any.

Keep it under about 12 lines. End by asking whether to start on the next step.

If the output says the folder isn't a ThreadBack project yet, say so in one line and mention that
`/threadback-save` will start tracking it.
