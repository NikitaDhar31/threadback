export type Task = { n: number; text: string }

export type ProjectView = {
  name: string
  next: string
  state: string
  tasks: Task[]
  blockers: string[]
  unsaved: number
  updated: string
}

declare module 'claude-code' {
  interface PluginState {
    'threadback-panel': { project: Shaped<ProjectView | null> }
  }
}
