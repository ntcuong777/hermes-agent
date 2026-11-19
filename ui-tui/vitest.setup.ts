// Importing src/sdk/apps starts the user-widget watcher on the real $HERMES_HOME. No test needs it,
// and the leaked watchers exhaust file descriptors (EMFILE) across the run, so it is a no-op here.
import { dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

import { vi } from 'vitest'

import { registerCompletionCaret } from '@hermes/shared/slash-input'
import { registerComposerEditor } from './src/sdk/ext/composerHooks.js'
import { registerCommandDispatchParser } from '@hermes/shared/slash'

const here = dirname(fileURLToPath(import.meta.url))
const dispatch = resolve(
  here,
  '../../../../../.worktrees/core-surface-all/hermes/plugins/tui-ports/tui/command-dispatch.mjs'
)
const mod = await import(pathToFileURL(dispatch).href)
const editor = await import(pathToFileURL(resolve(here, '../../../../../.worktrees/core-surface-all/hermes/plugins/tui-ports/tui/composer-editor.mjs')).href)

mod.register(registerCommandDispatchParser)
editor.register(registerComposerEditor)

const caret = await import(
  pathToFileURL(resolve(here, '../../../../../.worktrees/core-surface-all/hermes/plugins/tui-input/tui/completion-caret.mjs')).href
)

caret.register(registerCompletionCaret)

vi.mock('./src/sdk/userWidgets.js', async importOriginal => ({
  ...(await importOriginal<typeof import('./src/sdk/userWidgets.js')>()),
  watchUserWidgets: () => {}
}))
