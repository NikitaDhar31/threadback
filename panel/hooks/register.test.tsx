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

// Nothing sits beneath the plugin: each hook here stands for the engine.
// `runs` collects the commands the panel's buttons ran.
function engine(on: any, cwd: string, runs: string[][] = [], answer = { exitCode: 0, stdout: 'ok', stderr: '' }) {
  on('command.register', () => ({ value: { command: 'threadback-panel' } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('clock.every', () => ({ value: undefined }))
  on('session.start', (_: unknown, e: { cwd: string }) => ({ cwd: e.cwd }))
  on('session.cwd', () => ({ value: cwd }))
  on('env.get', (_: unknown, e: { name: string }) => ({
    value: e.name === 'THREADBACK_HOME' ? 'H' : undefined,
  }))
  on('fs.exists', () => ({ value: true }))
  on('fs.list', () => ({
    value: Object.keys(SAVED).map(name => ({ name, kind: 'file', size: 1, mtimeMs: 0, isLink: false })),
  }))
  on('fs.read', (_: unknown, e: { path: string }) => ({
    value: e.path.endsWith('tb-path')
      ? 'C:/plugins/threadback/scripts/tb.py\n'
      : JSON.stringify(SAVED[Object.keys(SAVED).find(name => e.path.endsWith(name)) as string]),
  }))
  on('process.run', (_: unknown, e: { argv: string[] }) => {
    if (e.argv[0] === 'py') throw new Error('py not installed') // falls back to python3
    runs.push([...e.argv])
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
    expect((await ui.find({ type: 'Text', text: /without saving/ }))?.text).toMatch(/^2 chats/)
    await ui.unmount()
  }
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
    const tb = ['python3', 'C:/plugins/threadback/scripts/tb.py', 'task']
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
