/** The live dependencies of the host (gateway, UI store, widget SDK, host modules): shared by the early load and the React adapter. */
import type { UiState } from '../../app/interfaces.js'
import { $uiState } from '../../app/uiStore.js'
import { recordParentLifecycle } from '../../lib/parentLog.js'
import { widgetSdk } from '../userWidgets.js'

import type { GatewayLike } from './api.js'
import type { StateSnapshot } from './contract.js'
import type { HostDeps } from './host.js'
import { HOST_MODULES } from './hostModules.js'
import { defaultStatePath } from './store.js'
import { type RunningWatchdog, startWatchdog } from './watchdog.js'

export const snapshotOf = (
  ui: Pick<UiState, 'busy' | 'gatewayConnected' | 'info' | 'sessionTitle' | 'sid' | 'status' | 'storedSid'>
): StateSnapshot =>
  Object.freeze({
    busy: ui.busy,
    connected: ui.gatewayConnected !== false,
    cwd: ui.info?.cwd ?? '',
    model: ui.info?.model ?? '',
    sid: ui.sid,
    status: ui.status,
    storedSid: ui.storedSid,
    title: ui.sessionTitle
  })

/** The watchdog thread starts at the first extension call, so a TUI without extensions never pays for it. */
const lazyWatchdog = (make: () => RunningWatchdog): RunningWatchdog => {
  let real: null | RunningWatchdog = null

  return {
    enter: id => (real ??= make()).enter(id),
    leave: () => real?.leave(),
    stop: () => real?.stop()
  }
}

const watchdogWanted = (): boolean => !process.env.VITEST && process.env.HERMES_TUI_EXT_WATCHDOG !== '0'

let sharedWatchdog: RunningWatchdog | undefined

export const stopSharedWatchdog = (): void => {
  sharedWatchdog?.stop()
  sharedWatchdog = undefined
}

export const buildHostDeps = (gw: GatewayLike, notify: (text: string) => void): HostDeps => {
  sharedWatchdog ??= watchdogWanted() ? lazyWatchdog(() => startWatchdog({ statePath: defaultStatePath() })) : undefined

  return {
    gw,
    host: HOST_MODULES,
    log: recordParentLifecycle,
    notify: text => notify(text),
    sdk: widgetSdk,
    state: {
      get: () => snapshotOf($uiState.get()),
      subscribe: listener => $uiState.listen(ui => listener(snapshotOf(ui)))
    },
    watchdog: sharedWatchdog
  }
}
