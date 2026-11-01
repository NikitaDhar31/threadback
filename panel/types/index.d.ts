/** `n` is the task's number in the project's full list, done tasks included:
 *  what `tb task done N` and the other edits take. */
export type Task = { n: number; text: string; done: boolean }

export type ProjectView = {
  slug: string
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
    'threadback-panel': {
      project: Shaped<ProjectView | null>
      showDone: Shaped<boolean>
      error: Shaped<string>
      flash: Shaped<boolean>
    }
  }
}
