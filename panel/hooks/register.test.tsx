import { expect, test } from 'claude-code/testing'

const PANE = {
  plugin: 'threadback-panel',
  component: 'Pane',
  requestId: 'threadback',
  props: {
    title: 'ThreadBack',
    isFocused: false,
    bodyColumns: 40,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 30 },
    view: {},
  },
} as const

const SAVED: Record<string, object> = {
  'outer.json': { name: 'Outer', slug: 'outer', folder: 'D:\\Work', next_action: 'wrong one', tasks: [] },
  'app.json': {
    name: 'My App',
    slug: 'my-app',
    folder: 'D:/Work/App/',
    updated: '2026-11-01 21:03:47',
    next_action: 'Write the login page',
    last_state: 'Signup works',
    tasks: [
      { text: 'Old task', done: true },
      { text: 'Add tests', done: false },
    ],
    blockers: [],
    auto_saves: [{}, {}],
  },
}

const TB = 'C:/Users/me/.claude/plugins/cache/threadback/threadback/0.7.16/scripts/tb.py'
const ENV: Record<string, string> = { THREADBACK_HOME: 'C:/Users/me/.threadback', USERPROFILE: 'C:/Users/me' }

// Nothing sits beneath the plugin: each hook here stands for the engine.
// `runs` collects the commands the panel's buttons ran; `seen` what else it did.
type Seen = { reads: string[]; cwds: (string | undefined)[] }
function engine(on: any, cwd: string, runs: string[][] = [], answer = { exitCode: 0, stdout: 'ok', stderr: '' },
                saved: Record<string, object> = SAVED, links: Record<string, string> = {},
                over: { env?: Record<string, string>; tbPath?: string } = {}, seen: Seen = { reads: [], cwds: [] }) {
  const env: Record<string, string> = { ...ENV, ...over.env }
  // $.fs.stat with resolve: where a path really lands (links followed); others stay as given.
  // A test host on Windows makes '/work/app' absolute as 'D:\\work\\app' before this hook sees
  // it; for a Mac/Linux path the mock answers as a Mac would, with the path as it was.
  const posix = cwd.startsWith('/')
  on('fs.stat', (_: unknown, e: { path: string }) => {
    const path = posix ? e.path.replace(/^[A-Za-z]:/, '').replace(/\\/g, '/') : e.path
    return { value: { kind: 'dir', size: 0, mtimeMs: 0, isLink: path in links, realPath: links[path] ?? path } }
  })
  on('command.register', () => ({ value: { command: 'threadback-panel' } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('clock.every', () => ({ value: undefined }))
  on('session.start', (_: unknown, e: { cwd: string }) => ({ cwd: e.cwd }))
  on('session.cwd', () => ({ value: cwd }))
  on('env.get', (_: unknown, e: { name: string }) => ({ value: env[e.name] }))
  on('fs.exists', () => ({ value: true }))
  on('fs.list', () => ({
    value: Object.keys(saved).map(name => ({ name, kind: 'file', size: 1, mtimeMs: 0, isLink: false })),
  }))
  on('fs.read', (_: unknown, e: { path: string }) => {
    seen.reads.push(e.path)
    return {
      value: e.path.endsWith('tb-path')
        ? `${over.tbPath ?? TB}\n`
        : JSON.stringify(saved[Object.keys(saved).find(name => e.path.endsWith(name)) as string]),
    }
  })
  on('process.run', (_: unknown, e: { argv: string[]; init?: { cwd?: string } }) => {
    if (e.argv[0] === 'py') throw new Error('py not installed') // falls back to python3
    runs.push([...e.argv])
    seen.cwds.push(e.init?.cwd)
    return { value: answer }
  })
}

test('shows the deepest matching project: next step, state, open tasks', async ($, on) => {
  engine(on, 'D:\\work\\app\\src')
  await $.session.start({ cwd: 'D:\\work\\app\\src', surface: 'desktop', isInteractive: true })

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    expect((await ui.find({ type: 'Text', text: /^My App$/ }))?.text).toBe('My App')
    expect((await ui.find({ type: 'Text', text: /login page/ }))?.text).toBe('Write the login page')
    expect((await ui.find({ type: 'Text', text: /Add tests/ }))?.text).toBe('Add tests')
    expect(await ui.find({ type: 'Text', text: /Old task/ })).toBeUndefined()
    expect((await ui.find({ type: 'Text', text: /Open tasks/ }))?.text).toBe('Open tasks (1)')
    expect((await ui.find({ type: 'Text', text: /unsaved messages/ }))?.text).toMatch(/^2 chats with/)
    await ui.unmount()
  }
})

test('on macOS and Linux, folders that differ only in case are different projects', async ($, on) => {
  const runs: string[][] = []
  const saved = {
    'upper.json': { name: 'Upper', slug: 'upper', folder: '/work/App', next_action: 'upper step', tasks: [] },
    'lower.json': {
      name: 'Lower', slug: 'lower', folder: '/work/app', next_action: 'lower step',
      tasks: [{ text: 'Lower task', done: false }],
    },
  }
  engine(on, '/work/app/src', runs, undefined, saved)
  await $.session.start({ cwd: '/work/app/src', surface: 'desktop', isInteractive: true })

  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect((await ui.find({ type: 'Text', text: /^Lower$/ }))?.text).toBe('Lower')
  expect(await ui.find({ type: 'Text', text: /upper step/ })).toBeUndefined()
  await ui.press({ key: 'done-1' })
  expect(runs.at(-1)?.slice(-2)).toEqual(['--project', 'lower'])  // the buttons edit the project shown
  await ui.unmount()
})

test('a chat opened through a link finds the project, as the core does', async ($, on) => {
  const saved = {
    'linked.json': { name: 'Linked', slug: 'linked', folder: '/private/var/work/site', next_action: 'go', tasks: [] },
  }
  // macOS: /var is a link to /private/var; the session reports the spelling the user typed.
  engine(on, '/var/work/site/src', [], undefined, saved, { '/var/work/site/src': '/private/var/work/site/src' })
  await $.session.start({ cwd: '/var/work/site/src', surface: 'desktop', isInteractive: true })

  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect((await ui.find({ type: 'Text', text: /^Linked$/ }))?.text).toBe('Linked')
  await ui.unmount()
})

test('a project with no open tasks shows no tasks section', async ($, on) => {
  engine(on, 'D:\\work')
  await $.session.start({ cwd: 'D:\\work', surface: 'desktop', isInteractive: true })

  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect((await ui.find({ type: 'Text', text: /^Outer$/ }))?.text).toBe('Outer')
  expect(await ui.find({ type: 'Text', text: /Open tasks/ })).toBeUndefined()
  await ui.unmount()
})

test('a folder ThreadBack does not track says how to start', async ($, on) => {
  engine(on, 'C:\\elsewhere')
  await $.session.start({ cwd: 'C:\\elsewhere', surface: 'desktop', isInteractive: true })

  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect((await ui.find({ type: 'Text', text: /isn't a ThreadBack project/ }))).toBeDefined()
  await ui.unmount()
})

test('buttons and the add box edit tasks through tb.py', async ($, on) => {
  const runs: string[][] = []
  engine(on, 'D:\\work\\app', runs)
  await $.session.start({ cwd: 'D:\\work\\app', surface: 'desktop', isInteractive: true })

  for (const surface of ['terminal', 'desktop'] as const) {
    runs.length = 0
    const ui = await $.ui.mount({ ...PANE, surface })
    await ui.press({ key: 'done-2' })
    await ui.press({ key: 'next-2' })
    await ui.press({ key: 'remove-2' })
    await ui.input({ key: 'add', text: '  Write docs ' })
    const tail = ['--project', 'my-app']
    const tb = ['python3', TB, 'task']
    expect(runs).toEqual([
      [...tb, 'done', '2', ...tail],
      [...tb, 'next', '2', ...tail],
      [...tb, 'remove', '2', ...tail],
      [...tb, 'add', 'Write docs', ...tail],
    ])
    await ui.unmount()
  }
})

test('Do next highlights the Next step section for a moment', async ($, on) => {
  engine(on, 'D:\\work\\app')
  // A timer's fn runs once its dispatch resolves; this one never does, so the
  // highlight stays up for the test to see (the real engine resolves it in 2.5 s).
  on('clock.after', () => new Promise(() => undefined))
  await $.session.start({ cwd: 'D:\\work\\app', surface: 'desktop', isInteractive: true })

  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect(await ui.find({ type: 'Text', text: /moved here/ })).toBeUndefined()
  await ui.press({ key: 'next-2' })
  expect(await ui.find({ type: 'Text', text: /moved here/ })).toBeDefined()
  await ui.unmount()
})

test('done tasks fold away and come back crossed out', async ($, on) => {
  engine(on, 'D:\\work\\app')
  await $.session.start({ cwd: 'D:\\work\\app', surface: 'desktop', isInteractive: true })

  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect(await ui.find({ type: 'Text', text: /Old task/ })).toBeUndefined()
  await ui.press({ key: 'toggle-done' })
  expect((await ui.find({ type: 'Text', text: /Old task/ }))?.text).toBe('Old task')
  expect(await ui.find({ type: 'Button', key: 'undo-1' })).toBeDefined()
  await ui.unmount()
})

test('a failed edit shows why', async ($, on) => {
  engine(on, 'D:\\work\\app', [], { exitCode: 1, stdout: '', stderr: 'No task number 2 in My App.' })
  await $.session.start({ cwd: 'D:\\work\\app', surface: 'desktop', isInteractive: true })

  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.press({ key: 'done-2' })
  expect((await ui.find({ type: 'Text', text: /No task number/ }))?.text).toBe('No task number 2 in My App.')
  await ui.unmount()
})

test('tb.py runs from its own folder, never from the project folder', async ($, on) => {
  // Windows finds `py` / `python3` in the current folder first, and a project could ship one.
  const seen: Seen = { reads: [], cwds: [] }
  engine(on, 'D:\\work\\app', [], undefined, SAVED, {}, {}, seen)
  await $.session.start({ cwd: 'D:\\work\\app', surface: 'desktop', isInteractive: true })

  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.press({ key: 'done-2' })
  expect(seen.cwds).toEqual([TB.replace(/\/tb\.py$/, '')])
  await ui.unmount()
})

// tb-path sits in the data folder; a project must not be able to name what runs.
for (const tbPath of ['D:/work/app/evil/scripts/tb.py', 'C:/Users/me/.claude/plugins/../x/scripts/tb.py',
                      'C:/Users/me/.claude/plugins/cache/x/evil.py', 'scripts/tb.py']) {
  test(`a tb-path outside Claude Code's plugins folder is never run: ${tbPath}`, async ($, on) => {
    const runs: string[][] = []
    engine(on, 'D:\\work\\app', runs, undefined, SAVED, {}, { tbPath })
    await $.session.start({ cwd: 'D:\\work\\app', surface: 'desktop', isInteractive: true })
    const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
    await ui.press({ key: 'done-2' })
    expect(runs).toEqual([])
    expect(await ui.find({ type: 'Text', text: /won't run it/ })).toBeDefined()
    await ui.unmount()
  })
}

// A project's own .claude/settings.json can set THREADBACK_HOME for the session.
test('a relative THREADBACK_HOME is ignored', async ($, on) => {
  const seen: Seen = { reads: [], cwds: [] }
  engine(on, 'D:\\work\\app', [], undefined, SAVED, {}, { env: { THREADBACK_HOME: '.tb' } }, seen)
  await $.session.start({ cwd: 'D:\\work\\app', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect(seen.reads.length).toBeGreaterThan(0)
  expect(seen.reads.filter(path => !path.replace(/\\/g, '/').startsWith('C:/Users/me/.threadback/'))).toEqual([])
  await ui.unmount()
})

test('a THREADBACK_HOME inside the project is not read', async ($, on) => {
  const seen: Seen = { reads: [], cwds: [] }
  engine(on, 'D:\\work\\app', [], undefined, SAVED, {}, { env: { THREADBACK_HOME: 'D:/work/app/.tb' } }, seen)
  await $.session.start({ cwd: 'D:\\work\\app', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect(seen.reads).toEqual([])
  expect(await ui.find({ type: 'Text', text: /isn't a ThreadBack project/ })).toBeDefined()
  await ui.unmount()
})
