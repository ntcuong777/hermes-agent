/** The one React adapter: attaches the host to the live gateway and UI store, and refreshes the listing when a session is up. */
import { useStore } from '@nanostores/react'
import { useEffect, useRef } from 'react'

import { $uiState } from '../../app/uiStore.js'
import type { GatewayClient } from '../../gatewayClient.js'

import { extensionHost } from './host.js'
import { buildHostDeps, snapshotOf, stopSharedWatchdog } from './hostDeps.js'

export { snapshotOf }

export function useExtensionHost(gw: GatewayClient, notify: (text: string) => void): void {
  const { gatewayConnected, sid } = useStore($uiState)
  const notifyRef = useRef(notify)

  notifyRef.current = notify

  useEffect(() => {
    // A second attach after the early load keeps the mounted slots and only swaps the live dependencies.
    extensionHost.attach(buildHostDeps(gw, text => notifyRef.current(text)))

    return () => {
      extensionHost.detach()
      stopSharedWatchdog()
    }
  }, [gw])

  useEffect(() => {
    if (sid && gatewayConnected !== false) {
      void extensionHost.refresh()
    }
  }, [sid, gatewayConnected])
}
