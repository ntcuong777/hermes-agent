import { describeRpcError } from '../app/userMessages.js'
import { t } from '../i18n/runtime.js'

export type RpcResult = Record<string, any>

export const asRpcResult = <T extends RpcResult = RpcResult>(value: unknown): T | null =>
  !value || typeof value !== 'object' || Array.isArray(value) ? null : (value as T)

// Every `error: …` line the TUI prints for a failed RPC goes through here, so
// transport-level failures (backend down, stale session id, version skew,
// timeouts) read as what happened + what to do instead of the wire text.
export const rpcErrorMessage = (err: unknown) =>
  err instanceof Error && err.message
    ? describeRpcError(err)
    : typeof err === 'string' && err.trim()
      ? describeRpcError(err)
      : t('rpc.requestFailed')

// `/compact` blocks its RPC for the whole minutes-long summarization — the
// default request deadline (120s) fired ~1s before a real compression
// committed, so the user saw a false timeout while the session had in fact
// been compacted. Same budget as Desktop's SESSION_COMPRESS_TIMEOUT_MS: it
// must sit above the gateway's compute-host wait cap (630s,
// tui_gateway/server.py _COMPUTE_HOST_COMPRESS_WAIT_CAP_SECS) or the client
// reports a false timeout while the host is still compressing (#97948).
export const SESSION_COMPRESS_TIMEOUT_MS = 660_000
