// Importing src/sdk/apps starts the user-widget watcher on the real $HERMES_HOME. No test needs it,
// and the leaked watchers exhaust file descriptors (EMFILE) across the run, so it is a no-op here.
import { vi } from 'vitest'

vi.mock('./src/sdk/userWidgets.js', async importOriginal => ({
  ...(await importOriginal<typeof import('./src/sdk/userWidgets.js')>()),
  watchUserWidgets: () => {}
}))
