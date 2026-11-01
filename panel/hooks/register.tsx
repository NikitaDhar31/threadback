import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { ProjectView } from '../types'

const PANE = 'threadback'
const TITLE = 'ThreadBack'
const REFRESH_MS = 30_000 // picks up saves made from the welcome window
// Change the tag when ProjectView's fields change: it declines what an earlier
// version stored, which outlives a reload.
const project = atom({ plugin: 'threadback-panel', key: 'project' } as const, null, {
  shape: 'project-1',
})

type Saved = {
  name: string
  folder?: string
  status?: string
  updated?: string
  next_action?: string
  last_state?: string
  tasks?: { text: string; done: boolean }[]
  blockers?: { text: string }[]
  auto_saves?: unknown[]
}

const norm = (path: string) => path.replace(/\//g, '\\').replace(/\\+$/, '').toLowerCase()

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
  const cwd = norm(await $.session.cwd())
  let best: Saved | null = null
  for (const entry of await $.fs.list(dir)) {
    if (entry.kind !== 'file' || !entry.name.endsWith('.json')) continue
    let p: Saved
    try {
      p = JSON.parse(await $.fs.read(`${dir}/${entry.name}`))
    } catch {
      continue
    }
    if (!p.folder) continue
    const folder = norm(p.folder)
    const holds = cwd === folder || cwd.startsWith(folder + '\\')
    if (holds && (best === null || folder.length > norm(best.folder ?? '').length)) best = p
  }
  if (best === null) return null
  return {
    name: best.name,
    next: best.next_action ?? '',
    state: best.last_state ?? '',
    tasks: (best.tasks ?? [])
      .map((t, i) => ({ n: i + 1, text: t.text, done: t.done }))
      .filter(t => !t.done)
      .map(({ n, text }) => ({ n, text })),
    blockers: (best.blockers ?? []).map(b => b.text),
    unsaved: (best.auto_saves ?? []).length,
    updated: best.updated ?? '',
  }
}

async function refresh($: EngineInterface): Promise<void> {
  const view = await load($).catch(() => null)
  await update($, project, () => view)
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
    const { Box, Text } = $.ui.resolve(e)
    const p = await read($, project)

    if (p === null) {
      return (
        <Box flexDirection="column">
          <Text dimColor>This folder isn't a ThreadBack project yet.</Text>
          <Text dimColor>Run /threadback-save to start tracking it.</Text>
        </Box>
      )
    }

    return (
      <Box flexDirection="column" gap={1}>
        <Box flexDirection="column">
          <Text bold>{p.name}</Text>
          {p.updated !== '' && <Text dimColor>Saved {p.updated}</Text>}
        </Box>
        <Box flexDirection="column">
          <Text bold color="suggestion">Next step</Text>
          <Text>{p.next || 'No next step saved yet.'}</Text>
        </Box>
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
        {p.tasks.length > 0 && (
          <Box flexDirection="column">
            <Text bold>Open tasks ({p.tasks.length})</Text>
            {p.tasks.map(t => <Text>{t.n}. {t.text}</Text>)}
          </Box>
        )}
        {p.unsaved > 0 && (
          <Text color="warning">
            {p.unsaved} chat{p.unsaved === 1 ? '' : 's'} ended without saving - run /threadback-save
          </Text>
        )}
      </Box>
    )
  })
}
