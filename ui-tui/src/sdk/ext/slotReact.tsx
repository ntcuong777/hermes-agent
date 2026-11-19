/**
 * The three adapters core code puts at a use site, one line each:
 *
 *   const SessionSwitcher = slotComponent('sessionSwitcher', StockSessionSwitcher)   // a component
 *   const request = slotFn('completion.request', stockRequest)(input, caret)         // a function
 *   const useCompletion = slotHook('completion.hook', stockUseCompletion)            // a hook
 *
 * With no extension mounted for the id, every adapter is the stock code (one map lookup). An extension's
 * replacement runs behind a boundary: a throw, or a call slower than SLOW_MS, is reported to the host, which
 * counts it, switches the slot off after SLOT_MAX_FAILURES (persisted) and lets upstream's own code run again.
 */
import { Component, type ComponentType, type ReactNode, useRef, useSyncExternalStore } from 'react'

import { extensionHost, type Host } from './host.js'

/** A function slot slower than this counts as a failure (it runs on the render path). */
export const SLOW_MS = 250

const messageOf = (error: unknown): string => (error instanceof Error ? error.message : String(error))

interface BoundaryProps {
  id: string
  host: Host
  impl: ComponentType<any>
  props: object
  stock: ComponentType<any>
}

/** Renders the extension component; a throw while rendering it renders the stock component on the same props at once. */
export class SlotBoundary extends Component<BoundaryProps, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true }
  }

  componentDidCatch(error: unknown): void {
    this.props.host.slotFailed(this.props.id, `render: ${messageOf(error)}`)
  }

  render(): ReactNode {
    const { impl: Impl, props, stock: Stock } = this.props

    return this.state.failed ? <Stock {...props} /> : <Impl {...props} />
  }
}

export const slotComponent = <P extends object>(
  id: string,
  Stock: ComponentType<P>,
  host: Host = extensionHost
): ComponentType<P> => {
  const Slot = (props: P) => {
    useSyncExternalStore(host.subscribeSlots, host.slotEpoch)

    const hit = host.slotFor(id)

    return hit ? (
      <SlotBoundary
        host={host}
        id={id}
        impl={hit.impl as ComponentType<any>}
        key={hit.extId}
        props={props}
        stock={Stock}
      />
    ) : (
      <Stock {...props} />
    )
  }

  Slot.displayName = `Slot(${id})`

  return Slot
}

/**
 * `check` rejects a malformed answer (the stock function then answers). The extension function must be synchronous.
 * A throw, a rejected check or a slow call is reported; a throw or a bad answer falls back to `stock` for this call.
 */
export const slotFn =
  <A extends unknown[], R>(
    id: string,
    stock: (...args: A) => R,
    host: Host = extensionHost,
    check?: (value: R) => boolean
  ) =>
  (...args: A): R => {
    const hit = host.slotFor(id)

    if (!hit) {
      return stock(...args)
    }

    const started = performance.now()

    try {
      const value = (hit.impl as (...a: A) => R)(...args)
      const took = performance.now() - started

      if (took > SLOW_MS) {
        host.slotFailed(id, `blocked the UI for ${Math.round(took)}ms (limit ${SLOW_MS}ms)`)
      }

      if (check && !check(value)) {
        host.slotFailed(id, 'returned an answer the slot contract rejects')

        return stock(...args)
      }

      return value
    } catch (error) {
      host.slotFailed(id, messageOf(error))

      return stock(...args)
    }
  }

/**
 * A hook slot picks stock or extension ONCE per mount (React forbids changing the hooks a component calls).
 * If the extension hook throws, the slot is switched off for good (persisted) and the error propagates. While
 * the component is first mounting, React's own retry then renders it again and picks stock; a throw AFTER the
 * component mounted is fail-stop (the render fails; the next start runs stock). Prefer `slotFn` and `slotComponent`.
 */
export const slotHook =
  <A extends unknown[], R>(id: string, stock: (...args: A) => R, host: Host = extensionHost) =>
  (...args: A): R => {
    const chosen = useRef<((...a: A) => R) | null>(null)

    chosen.current ??= (host.slotFor(id)?.impl as ((...a: A) => R) | undefined) ?? stock

    try {
      return chosen.current(...args)
    } catch (error) {
      if (chosen.current !== stock) {
        host.slotFailed(id, `hook: ${messageOf(error)}`, { immediate: true })
      }

      throw error
    }
  }
