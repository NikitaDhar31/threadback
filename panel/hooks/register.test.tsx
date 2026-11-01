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
  'outer.json': { name: 'Outer', folder: 'D:\\Work', next_action: 'wrong one', tasks: [] },
  'app.json': {
    name: 'My App',
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
function engine(on: any, cwd: string) {
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
    value: JSON.stringify(SAVED[Object.keys(SAVED).find(name => e.path.endsWith(name)) as string]),
  }))
}

test('shows the deepest matching project: next step, state, open tasks', async ($, on) => {
  engine(on, 'D:\\work\\app\\src')
  await $.session.start({ cwd: 'D:\\work\\app\\src', surface: 'desktop', isInteractive: true })

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    expect((await ui.find({ type: 'Text', text: /^My App$/ }))?.text).toBe('My App')
    expect((await ui.find({ type: 'Text', text: /login page/ }))?.text).toBe('Write the login page')
    expect((await ui.find({ type: 'Text', text: /Add tests/ }))?.text).toBe('2. Add tests')
    expect(await ui.find({ type: 'Text', text: /Old task/ })).toBeUndefined()
    expect((await ui.find({ type: 'Text', text: /Open tasks/ }))?.text).toBe('Open tasks (1)')
    expect((await ui.find({ type: 'Text', text: /without saving/ }))?.text).toMatch(/^2 chats/)
    await ui.unmount()
  }
})

test('a folder ThreadBack does not track says how to start', async ($, on) => {
  engine(on, 'C:\\elsewhere')
  await $.session.start({ cwd: 'C:\\elsewhere', surface: 'desktop', isInteractive: true })

  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect((await ui.find({ type: 'Text', text: /isn't a ThreadBack project/ }))).toBeDefined()
  await ui.unmount()
})
