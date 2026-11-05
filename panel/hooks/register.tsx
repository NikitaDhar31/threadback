import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { ProjectView } from '../types'

const PANE = 'threadback'
const TITLE = 'ThreadBack'
const REFRESH_MS = 30_000 // picks up saves made from the welcome window
// Change a tag when that value's fields change: it declines what an earlier
// version stored, which outlives a reload.
const project = atom({ plugin: 'threadback-panel', key: 'project' } as const, null, {
  shape: 'project-2',
})
const showDone = atom({ plugin: 'threadback-panel', key: 'showDone' } as const, false, {
  shape: 'show-done-1',
})
const error = atom({ plugin: 'threadback-panel', key: 'error' } as const, '', {
  shape: 'error-1',
})
// True for a moment after "Do next", so the eye follows the task into Next step.
const flash = atom({ plugin: 'threadback-panel', key: 'flash' } as const, false, {
  shape: 'flash-1',
})
const FLASH_MS = 2_500

type Saved = {
  name: string
  slug: string
  folder?: string
  status?: string
  updated?: string
  next_action?: string
  last_state?: string
  tasks?: { text: string; done: boolean }[]
  blockers?: { text: string }[]
  auto_saves?: unknown[]
}

type Action = 'add' | 'done' | 'undo' | 'remove' | 'top' | 'next'

// Folder names ignore case on Windows only, as ThreadBack's own find_by_folder does:
// on macOS and Linux /work/App and /work/app are two different projects.
const isWindowsPath = (path: string) => /^[a-zA-Z]:[\\/]/.test(path) || path.startsWith('\\\\')

function norm(path: string): string {
  if (!isWindowsPath(path)) return path.replace(/\/+$/, '')
  return path.replace(/\//g, '\\').replace(/\\+$/, '').toLowerCase()
}

/** Where a folder really is, links followed, as the core's realpath does: on macOS /var is
 *  /private/var. A folder that can't be resolved (gone, no access) keeps its own spelling. */
async function real($: EngineInterface, path: string): Promise<string> {
  const stat = await $.fs.stat(path, { resolve: true }).catch(() => undefined)
  return norm(stat?.realPath ?? path)
}

async function dataDir($: EngineInterface): Promise<string> {
  const own = await $.env.get('THREADBACK_HOME')
  if (own) return own
  const home = (await $.env.get('USERPROFILE')) ?? (await $.env.get('HOME')) ?? ''
  return `${home}/.threadback`
}

/** The saved project whose folder is the session's folder or holds it (deepest wins),
 *  the same rule as ThreadBack's own find_by_folder. */
async function load($: EngineInterface): Promise<ProjectView | null> {
  const dir = `${await dataDir($)}/projects`
  if (!(await $.fs.exists(dir))) return null
  const cwd = await real($, await $.session.cwd())
  let best: Saved | null = null
  let bestFolder = ''
  for (const entry of await $.fs.list(dir)) {
    if (entry.kind !== 'file' || !entry.name.endsWith('.json')) continue
    let p: Saved
    try {
      p = JSON.parse(await $.fs.read(`${dir}/${entry.name}`))
    } catch {
      continue
    }
    if (!p.folder) continue
    const folder = await real($, p.folder)
    const sep = isWindowsPath(folder) ? '\\' : '/'
    const holds = cwd === folder || cwd.startsWith(folder + sep)
    if (holds && (best === null || folder.length > bestFolder.length)) {
      best = p
      bestFolder = folder
    }
  }
  if (best === null) return null
  return {
    slug: best.slug,
    name: best.name,
    next: best.next_action ?? '',
    state: best.last_state ?? '',
    tasks: (best.tasks ?? []).map((t, i) => ({ n: i + 1, text: t.text, done: t.done })),
    blockers: (best.blockers ?? []).map(b => b.text),
    unsaved: (best.auto_saves ?? []).length,
    updated: best.updated ?? '',
  }
}

async function refresh($: EngineInterface): Promise<void> {
  const view = await load($).catch(() => null)
  await update($, project, () => view)
}

/** One task edit through ThreadBack's own tb.py, so the file format, redaction and
 *  saving stay in one place. ThreadBack's session-start hook writes where tb.py is. */
async function act($: EngineInterface, slug: string, action: Action, arg: string): Promise<void> {
  const tb = (await $.fs.read(`${await dataDir($)}/tb-path`).catch(() => '')).trim()
  if (tb === '') {
    await update($, error, () => 'Open a new chat once so ThreadBack can tell the panel where it is installed.')
    return
  }
  for (const python of [['py', '-3'], ['python3'], ['python']]) {
    let ran
    try {
      ran = await $.process.run([...python, tb, 'task', action, arg, '--project', slug], {
        timeoutMs: 20_000,
      })
    } catch {
      continue // this interpreter isn't installed; try the next
    }
    const said = (ran.stderr || ran.stdout).trim()
    await update($, error, () => (ran.exitCode === 0 ? '' : said || `tb task ${action} failed.`))
    await refresh($)
    if (action === 'next' && ran.exitCode === 0) {
      await update($, flash, () => true)
      $.clock.after(FLASH_MS, () => update($, flash, () => false))
    }
    return
  }
  await update($, error, () => 'Python 3 was not found, so the panel cannot change tasks.')
}

let timer: { cancel: () => void } | undefined

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'threadback-panel',
      description: "Show this project's ThreadBack next step and tasks beside the chat",
    })
    await refresh($)
    void $.ui.open({ id: PANE, title: TITLE })
    timer?.cancel()
    timer = $.clock.every(REFRESH_MS, () => refresh($))

    return next(e)
  })

  on('command.run', { command: 'threadback-panel' }, async $ => {
    await refresh($)
    await $.ui.open({ id: PANE, title: TITLE })

    return { text: 'ThreadBack panel opened.' }
  })

  // /threadback-save and the tb script run through Bash: redraw once they finish.
  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    const ran = await next(e)
    if (/threadback|scripts\/tb/i.test(e.command)) await refresh($).catch(() => undefined)

    return ran
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button, Input } = $.ui.resolve(e)
    const p = await read($, project)
    const problem = await read($, error)

    if (p === null) {
      return (
        <Box flexDirection="column">
          <Text dimColor>This folder isn't a ThreadBack project yet.</Text>
          <Text dimColor>Run /threadback-save to start tracking it.</Text>
        </Box>
      )
    }

    const edit = (action: Action, arg: string | number) => () => void act($, p.slug, action, String(arg))
    const open = p.tasks.filter(t => !t.done)
    const done = p.tasks.filter(t => t.done)
    const isShowingDone = await read($, showDone)
    const isFlashing = await read($, flash)

    return (
      <Box flexDirection="column" gap={1}>
        <Box flexDirection="column">
          <Text bold>{p.name}</Text>
          {p.updated !== '' && <Text dimColor>Saved {p.updated}</Text>}
        </Box>
        {isFlashing ? (
          <Box key="next-step" flexDirection="column" borderStyle="round" borderColor="suggestion" paddingX={1}>
            <Box flexDirection="row" gap={2}>
              <Text bold color="suggestion">Next step</Text>
              <Text dimColor>moved here</Text>
            </Box>
            <Text bold>{p.next}</Text>
          </Box>
        ) : (
          <Box key="next-step" flexDirection="column">
            <Text bold color="suggestion">Next step</Text>
            <Text>{p.next || 'No next step saved yet.'}</Text>
          </Box>
        )}
        {p.state !== '' && (
          <Box flexDirection="column">
            <Text bold>Where you left off</Text>
            <Text dimColor>{p.state}</Text>
          </Box>
        )}
        {p.blockers.length > 0 && (
          <Box flexDirection="column">
            <Text bold color="error">Blockers</Text>
            {p.blockers.map(b => <Text>- {b}</Text>)}
          </Box>
        )}
        <Box flexDirection="column">
          {open.length > 0 && <Text bold>Open tasks ({open.length})</Text>}
          {/* A narrow pane wraps each task over several lines: the text gets the full
              width, ☐ sits on its first line, the actions go on a dim line beneath,
              and a blank line separates one task from the next. */}
          {open.map((t, i) => (
            <Box key={`task-${t.n}`} flexDirection="column" marginTop={i > 0 ? 1 : 0}>
              <Box flexDirection="row" gap={1} alignItems="flex-start">
                <Button key={`done-${t.n}`} label="☐" plain onPress={edit('done', t.n)} />
                <Box flexGrow={1} flexShrink={1}>
                  <Text wrap="wrap">{t.text}</Text>
                </Box>
              </Box>
              <Box flexDirection="row" gap={2} marginLeft={3}>
                {i > 0 && <Button key={`top-${t.n}`} label="↑" plain dimColor onPress={edit('top', t.n)} />}
                <Button key={`next-${t.n}`} label="Do next" plain dimColor onPress={edit('next', t.n)} />
                <Button key={`remove-${t.n}`} label="✕" plain dimColor onPress={edit('remove', t.n)} />
              </Box>
            </Box>
          ))}
          {done.length > 0 && (
            <Box marginTop={1}>
              <Button
                key="toggle-done"
                label={`${isShowingDone ? 'Hide' : 'Show'} done (${done.length})`}
                plain
                dimColor
                onPress={() => void update($, showDone, shown => !shown)}
              />
            </Box>
          )}
          {isShowingDone &&
            done.map(t => (
              <Box key={`task-${t.n}`} flexDirection="row" gap={1} alignItems="flex-start" marginTop={1}>
                <Button key={`undo-${t.n}`} label="☑" plain dimColor onPress={edit('undo', t.n)} />
                <Box flexGrow={1} flexShrink={1}>
                  <Text wrap="wrap" dimColor strikethrough>{t.text}</Text>
                </Box>
              </Box>
            ))}
          <Box marginTop={1}>
            <Input
              key="add"
              placeholder="Add a task"
              submitLabel="add"
              value=""
              onSubmit={text => {
                if (text.trim() !== '') void act($, p.slug, 'add', text.trim())
              }}
            />
          </Box>
        </Box>
        {problem !== '' && <Text color="error">{problem}</Text>}
        {p.unsaved > 0 && (
          <Text color="warning">
            {p.unsaved} chat{p.unsaved === 1 ? '' : 's'} with unsaved messages - run /threadback-save
          </Text>
        )}
      </Box>
    )
  })
}
