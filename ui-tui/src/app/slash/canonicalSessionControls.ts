import { randomUUID } from 'node:crypto'

import { introMsg, toTranscriptMessages } from '../../domain/messages.js'
import { CANONICAL_REASONING_EFFORTS, TUI_SESSION_MODEL_FLAG } from '../../domain/slash.js'
import { t } from '../../i18n/runtime.js'
import { asRpcResult, rpcErrorMessage } from '../../lib/rpc.js'
import { patchOverlayState } from '../overlayStore.js'
import { getUiState, patchUiState } from '../uiStore.js'

import type { SlashRunCtx } from './types.js'

interface MutationGateway {
  request: (method: string, params?: Record<string, unknown>) => Promise<unknown>
}

// Keep the original CAS tuple after a lost reply. Reissuing the same command
// must query that receipt, not silently authorize another write at a new revision.
const pending = new WeakMap<MutationGateway, Map<string, Record<string, unknown>>>()

// The levels `hermes_constants.parse_reasoning_effort` accepts; the display words are config.yaml writes.
export const REASONING_EFFORTS = new Set([
  'none',
  'false',
  'disabled',
  'minimal',
  'low',
  'medium',
  'high',
  'xhigh',
  'max',
  'ultra'
])

/** `<model> [--provider <p>] [--reasoning <level>] [--session]`. The owner's `model` mutation carries the model
 *  and provider only; the level is its own `reasoning` mutation, so it is split out here. The model picker's
 *  third step emits `--reasoning`, so refusing it refused every pick that chose a level. */
function modelPayload(arg: string) {
  const parts = arg.trim().split(/\s+/)
  const model = parts.shift()!
  const payload: Record<string, string> = { model }
  let effort = ''

  if (model.startsWith('--')) {
    throw new Error(t('canonical.controls.modelUsage'))
  }

  while (parts.length) {
    const flag = parts.shift()!

    if (flag === '--session' || flag === TUI_SESSION_MODEL_FLAG) {
      continue
    }

    if (flag === '--provider' && parts[0] && !parts[0].startsWith('--')) {
      payload.provider = parts.shift()!

      continue
    }

    // A picker-issued level (#138856) rides in the same selection: a busy session defers the pick
    // and commits model and level together. An unknown level is refused like any other flag.
    if (flag === '--reasoning' && CANONICAL_REASONING_EFFORTS.has(parts[0]?.toLowerCase() ?? '')) {
      payload.reasoning = parts.shift()!.toLowerCase()

      continue
    }

    throw new Error(t('canonical.controls.unsupportedModelOption', flag))
  }

  return { effort, payload }
}

export type CanonicalOperation = 'model' | 'branch' | 'compress' | 'delete' | 'rename' | 'rewind' | 'reasoning'

/** The snapshot a mutation's CAS tuple was read from; `payload` may derive its target from it. */
export interface MutationSnapshot {
  messages?: Array<Record<string, unknown>>
  /** The stored rows (content + row_id) behind a display-projected `messages`. */
  stored_messages?: Array<Record<string, unknown>>
  revision: number
  execution_generation: number
  /** The viewer attachment the revision read made (`session.resume` attaches). */
  subscription_id?: string
  /** The id the owner resolved the read to and subscribed under; may differ from the one sent. */
  session_id?: string
}

/** The CAS tuple read (`session.resume`, which attaches this client as a viewer). */
async function readCasTuple(gw: MutationGateway, sid: string, onRead?: (snapshot: MutationSnapshot) => void) {
  const snapshot = asRpcResult<MutationSnapshot>(await gw.request('session.resume', { session_id: sid }))

  if (snapshot) {
    onRead?.(snapshot)
  }

  return snapshot
}

/** One revision-fenced `session.mutate` (`gateway/session_mutations.py`). The CAS tuple comes from a
 * fresh `session.resume`; `payload` may be a function of that same snapshot (a rewind names its
 * target row from it) and return null for "nothing to do". Metadata edits (`rename`) carry no
 * generation fence, matching Desktop's `retainedMutation(..., withGeneration=false)`. */
/** Only a session's very next control may retry an ambiguous one: any other verb retires the rest. */
export function retireCanonicalControls(gw: MutationGateway, sid: string, keep?: string) {
  const requests = pending.get(gw)

  for (const key of [...(requests?.keys() ?? [])]) {
    if (key !== keep && JSON.parse(key)[0] === sid) {
      requests!.delete(key)
    }
  }
}

export async function mutateCanonical(
  gw: MutationGateway,
  sid: string,
  operation: CanonicalOperation,
  payload: Record<string, unknown> | ((snapshot: MutationSnapshot) => null | Record<string, unknown>),
  stale: () => boolean = () => false,
  onRead?: (snapshot: MutationSnapshot) => void
) {
  let requests = pending.get(gw)

  if (!requests) {
    requests = new Map()
    pending.set(gw, requests)
  }

  // A derived payload is keyed by operation alone: a lost-reply retry must re-present the
  // original target, never re-derive one from a transcript the first attempt already rewound.
  const key = JSON.stringify([sid, operation, typeof payload === 'function' ? null : payload])
  retireCanonicalControls(gw, sid, key)
  let params = requests.get(key)

  if (!params) {
    const snapshot = await readCasTuple(gw, sid, onRead)

    if (stale()) {
      return
    }

    if (!Number.isSafeInteger(snapshot?.revision) || !Number.isSafeInteger(snapshot?.execution_generation)) {
      throw new Error(t('canonical.controls.identityUnavailable'))
    }

    const body = typeof payload === 'function' ? payload(snapshot!) : payload

    if (!body) {
      return { result: null, expectedGeneration: snapshot!.execution_generation }
    }

    params = {
      session_id: sid,
      request_id: randomUUID(),
      expected_revision: snapshot!.revision,
      ...(operation === 'rename' ? {} : { expected_generation: snapshot!.execution_generation }),
      operation,
      payload: body
    }
    requests.set(key, params)
  }

  let result

  try {
    result = asRpcResult(await gw.request('session.mutate', params))

    if (!result) {
      throw new Error(t('session.common.invalidResponse', 'session.mutate'))
    }

    requests.delete(key)
  } catch (error) {
    // A structured authority refusal is definitive; transport errors are not.
    if ((error as { data?: { reason?: string } })?.data?.reason) {
      requests.delete(key)
    }

    throw error
  }

  return { result, expectedGeneration: (params.expected_generation ?? result.execution_generation) as number }
}

export async function mutateCanonicalSession(
  gw: MutationGateway,
  sid: string,
  operation: 'model' | 'branch' | 'compress',
  arg: string,
  stale: () => boolean = () => false,
  confirm?: string
) {
  // `confirm`: the owner's one-time token for a guarded model target the user accepted. It keys a
  // separate retained request, so an ambiguous reply to the confirmed send retries that exact one.
  const { effort, payload: body } =
    operation === 'model'
      ? modelPayload(arg)
      : { effort: '', payload: arg ? { [operation === 'branch' ? 'title' : 'focus']: arg } : {} }

  const payload = { ...body, ...(confirm ? { confirm } : {}) }
  const mutation = await mutateCanonical(gw, sid, operation, payload, stale)

  if (!mutation?.result) {
    return undefined
  }

  // The level follows only a switch that was applied: a refusal threw above, and a guarded target the owner
  // wants confirmed wrote nothing, so the confirmed re-send (same argument) carries the level then.
  if (!effort || mutation.result.status === 'confirmation_required') {
    return { result: mutation.result, expectedGeneration: mutation.expectedGeneration }
  }

  let level

  try {
    level = await mutateCanonical(gw, sid, 'reasoning', { effort }, stale)
  } catch (error) {
    throw new Error(t('canonical.controls.reasoningNotSet', mutation.result.model as string, (error as Error).message))
  }

  // The reasoning receipt is newer: report its generation, or the live session.info it published would mark
  // the model result as superseded and the switch would go unannounced.
  const generation = Math.max(
    Number(mutation.result.execution_generation ?? 0),
    Number(level?.result?.execution_generation ?? 0)
  )

  return {
    result: { ...mutation.result, execution_generation: generation },
    expectedGeneration: mutation.expectedGeneration
  }
}

type CanonicalControlResult = NonNullable<Awaited<ReturnType<typeof mutateCanonicalSession>>>['result']

interface ModelConfirmation {
  confirm: string
  confirm_message?: string
  status: 'confirmation_required'
  target_model?: string
}

const needsModelConfirmation = (result: CanonicalControlResult): result is ModelConfirmation =>
  result.status === 'confirmation_required' && typeof result.confirm === 'string'

/** The owner refused a guarded model target (cost / data policy / large context) and wrote
 *  nothing. Ask with the same dialog the legacy `config.set` path used; only "switch anyway"
 *  re-sends, once, with the owner's token. A confirmed send refused again (a turn or another
 *  switch landed first) is reported, never re-asked in a loop. */
function askModelConfirmation(refusal: ModelConfirmation, arg: string, ctx: SlashRunCtx, confirmed: boolean) {
  if (confirmed) {
    throw new Error(`${refusal.confirm_message ?? ''}\n\n${t('slashCmd.session.model.confirmStale')}`.trim())
  }

  openModelConfirmation(refusal.confirm_message, refusal.target_model ?? arg.trim().split(/\s+/)[0] ?? '', () =>
    void runCanonicalSessionControl('model', arg, ctx, refusal.confirm)
  )
}

/** The guarded-model dialog: an idle pick's refusal and a deferred pick's boundary ask share it. */
export function openModelConfirmation(detail: string | undefined, target: string, onConfirm: () => void) {
  patchOverlayState({
    confirm: {
      cancelLabel: t('slashCmd.session.model.cancel'),
      confirmLabel: t('slashCmd.session.model.switchAnyway'),
      danger: true,
      detail: detail || t('slashCmd.session.model.expensiveDetail'),
      onConfirm,
      title: t('slashCmd.session.model.confirmTitle', target)
    }
  })
}

/** `baseline` is the session info the control started from: an epoch change or a newer turn wins. */
function isSupersededControl(
  operation: 'model' | 'branch' | 'compress',
  result: CanonicalControlResult,
  expectedGeneration: number,
  baseline: ReturnType<typeof getUiState>['info']
) {
  const current = getUiState().info

  return (
    operation !== 'branch' &&
    (current?.execution_epoch !== baseline?.execution_epoch ||
      (current?.execution_generation ?? 0) > (result.execution_generation ?? expectedGeneration))
  )
}

async function applyBranchResult(result: CanonicalControlResult, arg: string, ctx: SlashRunCtx) {
  if (!result.branched_session_id) {
    throw new Error(t('session.common.invalidResponse', 'branch'))
  }

  // The child resume repaints the transcript; main's "branched →" line is printed after it, or the
  // reset wipes it.
  const child = result.branched_session_id as string

  await ctx.session.resumeById(child)

  // Main named the child by its title ("Slash lane #2" for an untitled branch of "Slash lane").
  const info = await ctx.gateway.gw
    .request<{ title?: null | string }>('session.info', { session_id: child })
    .catch(() => null)

  // The resume moved this view to the child, so the slash's own staleness check no longer applies:
  // print only while the child is still the view's session.
  if (getUiState().sid === child) {
    ctx.transcript.sys(t('slashCmd.session.branch.branched', asRpcResult(info)?.title?.trim() || arg || child))
  }
}

function applyModelResult(result: CanonicalControlResult, sys: (text: string) => void) {
  if (!result.model) {
    throw new Error(t('session.main.invalidModelSwitchResponse'))
  }

  if (result.status === 'deferred') {
    // A turn is running: the owner keeps the pick for the next turn, and session.info repaints the
    // model when it lands (or a refusal / confirmation follows on session.updated).
    sys(t('slashCmd.session.model.switchedDeferred', result.model))

    return
  }

  patchUiState(state => ({
    ...state,
    info: {
      ...state.info,
      model: result.model,
      execution_generation: Math.max(state.info?.execution_generation ?? 0, result.execution_generation ?? 0),
      skills: state.info?.skills ?? {},
      tools: state.info?.tools ?? {}
    }
  }))
  sys(t('session.main.modelSwitched', result.model))

  if (typeof result.effort === 'string') {
    // The level committed in the same selection (a deferred result carries none yet).
    sys(t('slashCmd.session.reasoning.current', result.effort))
  }
}

/** A model switch made outside `/model` (a window plan's limit card): the same revision-fenced
 *  `session.mutate` and guarded-target ask. `onApplied` runs only once the switch committed now,
 *  never for a refusal, a declined ask, or a pick deferred behind a running turn. Like `/model`, a
 *  reply that lands after the view moved to another chat (or a newer turn ran) applies nothing:
 *  `onApplied` re-sends via the active chat, so it must still be `sid`. */
export async function switchCanonicalModel(
  gw: MutationGateway,
  sid: string,
  arg: string,
  sys: (text: string) => void,
  onApplied: () => void,
  confirm?: string
): Promise<void> {
  const baseline = getUiState().info
  const stale = () => getUiState().sid !== sid
  const mutation = await mutateCanonicalSession(gw, sid, 'model', arg, stale, confirm)

  if (!mutation || stale() || isSupersededControl('model', mutation.result, mutation.expectedGeneration, baseline)) {
    return
  }

  const { result } = mutation

  if (needsModelConfirmation(result)) {
    if (confirm) {
      return sys(t('slashCmd.session.model.confirmStale'))
    }

    return openModelConfirmation(
      result.confirm_message,
      result.target_model ?? arg.trim().split(/\s+/)[0] ?? '',
      () =>
        void switchCanonicalModel(gw, sid, arg, sys, onApplied, result.confirm).catch((e: unknown) =>
          sys(`error: ${rpcErrorMessage(e)}`)
        )
    )
  }

  applyModelResult(result, sys)

  if (result.status !== 'deferred') {
    onApplied()
  }
}

type CompressSummary = { headline?: string; noop?: boolean; note?: string; token_line?: string } | null

/** The authority's report (headline, token line, note), as the native /compress prints it. */
function printCompressSummary(summary: CompressSummary, ctx: SlashRunCtx) {
  ctx.transcript.sys(summary?.headline ? `${summary.noop ? '' : '✓ '}${summary.headline}` : t('canonical.controls.compressed'))

  for (const line of [summary?.token_line, summary?.note]) {
    if (line) {
      ctx.transcript.sys(`  ${line}`)
    }
  }
}

async function applyCompressResult(gw: MutationGateway, sid: string, result: CanonicalControlResult, ctx: SlashRunCtx) {
  const before = getUiState()
  const snapshot = asRpcResult(await gw.request('session.resume', { session_id: sid }))

  if (ctx.stale() || getUiState().info !== before.info || getUiState().busy !== before.busy) {
    return
  }

  if (!snapshot || !Array.isArray(snapshot.messages)) {
    throw new Error(t('session.common.invalidResponse', 'compressed transcript'))
  }

  const info = { ...before.info, ...snapshot.info }

  if (
    info.execution_epoch !== before.info?.execution_epoch ||
    (info.execution_generation ?? -1) < (result.execution_generation ?? 0)
  ) {
    throw new Error(t('canonical.controls.compressedStale'))
  }

  ctx.transcript.setHistoryItems([introMsg(info), ...toTranscriptMessages(snapshot.messages)])
  patchUiState({ info })
  printCompressSummary(result.summary as CompressSummary, ctx)
}

export async function runCanonicalSessionControl(
  operation: 'model' | 'branch' | 'compress',
  arg: string,
  ctx: SlashRunCtx,
  confirm?: string
) {
  const gw = ctx.gateway.gw
  // The compress RPC answers only after its minutes-long summarization and the
  // commit: the status bar's compaction verb (the one auto-compaction already
  // shows) is the progress for that whole window, and the flag must drop on a
  // refusal too or the surface reads stalled forever.
  const showsCompacting = operation === 'compress'

  try {
    if (!ctx.sid) {
      throw new Error(t('slashCmd.core.status.noActiveSession'))
    }

    if (showsCompacting) {
      patchUiState({ compacting: true })
    }

    const mutation = await mutateCanonicalSession(gw, ctx.sid, operation, arg, ctx.stale, confirm)

    if (!mutation) {
      return
    }

    const { result, expectedGeneration } = mutation

    if (ctx.stale()) {
      return
    }

    if (isSupersededControl(operation, result, expectedGeneration, ctx.ui.info)) {
      return
    }

    if (operation === 'model' && needsModelConfirmation(result)) {
      askModelConfirmation(result, arg, ctx, Boolean(confirm))
    } else if (operation === 'branch') {
      await applyBranchResult(result, arg, ctx)
    } else if (operation === 'model') {
      applyModelResult(result, ctx.transcript.sys)
    } else if (result.status === 'preview') {
      // `--preview` is a read-only report; nothing to re-hydrate.
      ctx.transcript.sys((result.lines as string[]).join('\n'))
    } else if (result.status === 'noop') {
      // Nothing removable: main's "No changes from compression" report; the transcript is untouched.
      printCompressSummary(result.summary as CompressSummary, ctx)
    } else {
      await applyCompressResult(gw, ctx.sid, result, ctx)
    }
  } catch (error) {
    ctx.guardedErr(error)
  } finally {
    if (showsCompacting) {
      patchUiState({ compacting: false })
    }
  }
}
