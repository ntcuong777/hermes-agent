import { Box, Text, useInput, useStdout } from '@hermes/ink'
import { fuzzyRank } from '@hermes/shared/fuzzy'
import type { SessionListResult, SessionListRow } from '@hermes/shared/gateway-events'
import type { SessionActiveItem, SessionActiveListResponse, SessionCloseResponse } from '@hermes/shared/gateway-types'
import { sessionScopedModelArg } from '@hermes/shared/slash-input'
import { useCallback, useEffect, useRef, useState } from 'react'

import { $sessionsOverlayInputMode } from '../app/overlayStore.js'
import type { GatewayClient } from '../gatewayClient.js'
import { useSessionDelete } from '../hooks/useSessionDelete.js'
import { messages } from '../i18n/runtime.js'
import { useT } from '../i18n/useT.js'
import { asRpcResult, rpcErrorMessage } from '../lib/rpc.js'
import type { Theme } from '../theme.js'

import { ModelPicker } from './modelPicker.js'
import { windowOffset } from './overlayControls.js'
import { clampOverlayWidth, listRowStyle } from './overlayPrimitives.js'
import { TextInput } from './textInput.js'

const VISIBLE = 12
const MIN_WIDTH = 64
const MAX_WIDTH = 128
const TITLE_MAX = 64

const STATUS_GLYPH: Record<string, string> = {
  idle: '✓',
  starting: '…',
  waiting: '?',
  working: '▶'
}

/** Human label for a live-session status value; unknown statuses render as-is.
 *  Resolved at call time so it follows the active language. */
export const sessionStatusLabel = (status: string): string => {
  const labels: Record<string, string | undefined> = messages().pickers.session.status

  return labels[status] ?? status
}

const CTRL_OFFSET = 96

const shortModel = (model = '') => model.replace(/^.*\//, '') || messages().pickers.session.modelUnknown
const ctrlChar = (letter: string) => String.fromCharCode(letter.charCodeAt(0) - CTRL_OFFSET)

export const fixedSessionColumnStyle = () => ({ flexShrink: 0 })

export const activeSessionCountLabel = (count: number) =>
  count === 1 ? messages().pickers.session.liveSessionsOne(1) : messages().pickers.session.liveSessionsOther(count)

export const sessionsCountLabel = (liveCount: number, resumableCount: number) =>
  messages().pickers.session.counts(liveCount, resumableCount)

export type SessionRowKind = 'history' | 'live' | 'new'

/**
 * Map a flat row index into the merged Sessions list to its kind. Rows are
 * ordered [new][live…][history…] — the "+ new" row is pinned first so it is
 * always visible no matter how long the resumable history grows.
 */
export const sessionRowKindAt = (index: number, liveCount: number): SessionRowKind => {
  if (index <= 0) {
    return 'new'
  }

  return index - 1 < liveCount ? 'live' : 'history'
}

export const relativeSessionAge = (ts?: number) => {
  if (!ts) {
    return ''
  }

  const days = (Date.now() / 1000 - ts) / 86400

  const age = messages().pickers.session.age

  if (days < 1) {
    return age.today
  }

  if (days < 2) {
    return age.yesterday
  }

  return age.daysAgo(Math.floor(days))
}

/** Drop already-live sessions from the resumable history list (dedupe by id). */
export const resumableHistory = (history: readonly SessionListRow[], live: readonly SessionActiveItem[]) => {
  const liveIds = new Set(live.map(s => s.id))

  return history.filter(h => !liveIds.has(h.id))
}

/** Everything a row can be found by: words from the title/preview, the id, and
 *  — the reason resume filtering exists — which project a session belonged to
 *  (cwd / branch; live rows carry the model instead). */
export const sessionSearchText = (row: SessionActiveItem | SessionListRow): string =>
  [
    row.title,
    row.preview,
    row.id,
    'cwd' in row ? row.cwd : undefined,
    'git_branch' in row ? row.git_branch : undefined,
    'model' in row ? row.model : undefined
  ]
    .filter(Boolean)
    .join(' ')

export interface SessionLists {
  items: SessionActiveItem[]
  history: SessionListRow[]
}

/** Fuzzy-narrow both row lists by `query` (same ranker as the model picker).
 *  An empty query is the identity, returning the input arrays untouched. */
export const filterSessionLists = (
  items: readonly SessionActiveItem[],
  history: readonly SessionListRow[],
  query: string
): SessionLists => {
  if (!query.trim()) {
    return { items: items as SessionActiveItem[], history: history as SessionListRow[] }
  }

  return {
    items: fuzzyRank(items, query, sessionSearchText).map(r => r.item),
    history: fuzzyRank(history, query, sessionSearchText).map(r => r.item)
  }
}

// Hint segment tables are functions (not module constants) so their text
// fragments resolve against the active language at render time.
export const resumeRowContextHintSegments = (): OrchestratorHintSegment[] => {
  const h = messages().pickers.session.hint

  return [
    { role: 'label', text: h.resumableLabel },
    { role: 'text', text: ' ' },
    { role: 'hotkey', text: 'Enter' },
    { role: 'text', text: h.resume },
    { role: 'hotkey', text: 'd' },
    { role: 'text', text: h.delete },
    ...filterHintSuffixSegments()
  ]
}

/** `session.list` params for the switcher: the caller's workspace only while the filter is on. */
export const sessionListWorkspaceParams = (workspaceOnly: boolean, cwd: string): { cwd?: string } =>
  workspaceOnly && cwd ? { cwd } : {}

export const workspaceHintSegments = (workspaceOnly: boolean): OrchestratorHintSegment[] => {
  const h = messages().pickers.session.hint

  return [
    { role: 'hotkey', text: 'w' },
    { role: 'text', text: workspaceOnly ? h.allSessions : h.thisWorkspace }
  ]
}

export type OrchestratorHintRole = 'hotkey' | 'label' | 'text'

export interface OrchestratorHintSegment {
  role: OrchestratorHintRole
  text: string
}

export const orchestratorContextHintSegments = (newSelected: boolean): OrchestratorHintSegment[] => {
  const h = messages().pickers.session.hint

  return newSelected
    ? [
        { role: 'label', text: h.newRowLabel },
        { role: 'text', text: h.typePrompt },
        { role: 'hotkey', text: 'Enter' },
        { role: 'text', text: h.start },
        { role: 'hotkey', text: 'Tab' },
        { role: 'text', text: h.model },
        { role: 'hotkey', text: 'Ctrl+O' },
        { role: 'text', text: h.workspace }
      ]
    : [
        { role: 'label', text: h.sessionRowLabel },
        { role: 'text', text: ' ' },
        { role: 'hotkey', text: 'Enter' },
        { role: 'text', text: h.switch },
        { role: 'hotkey', text: 'Ctrl+D' },
        { role: 'text', text: h.close },
        ...filterHintSuffixSegments()
      ]
}

export const orchestratorGlobalHotkeyHintSegments = (): OrchestratorHintSegment[] => {
  const h = messages().pickers.session.hint

  return [
    { role: 'hotkey', text: '↑↓' },
    { role: 'text', text: h.move },
    { role: 'hotkey', text: 'Ctrl+N' },
    { role: 'text', text: h.new },
    { role: 'hotkey', text: 'Ctrl+R' },
    { role: 'text', text: h.refresh },
    { role: 'hotkey', text: 'Esc' },
    { role: 'text', text: h.close }
  ]
}

/** `/ find` is advertised on the row-context hint lines — it is a row action
 *  (it cannot open from the `+new` row) and the global hotkey line is already
 *  at its truncation width with the workspace segment appended. */
export const filterHintSuffixSegments = (): OrchestratorHintSegment[] => {
  const h = messages().pickers.session.hint

  return [
    { role: 'text', text: ' · ' },
    { role: 'hotkey', text: '/' },
    { role: 'text', text: h.find }
  ]
}

/** Hint line shown while the filter input is open: what a row does now. */
export const orchestratorFilterHintSegments = (onHistory: boolean): OrchestratorHintSegment[] => {
  const h = messages().pickers.session.hint

  return [
    { role: 'hotkey', text: '↑↓' },
    { role: 'text', text: h.move },
    { role: 'hotkey', text: 'Enter' },
    { role: 'text', text: onHistory ? h.resume : h.switch },
    { role: 'hotkey', text: 'Esc' },
    { role: 'text', text: h.clearBack }
  ]
}

const hintText = (segments: readonly OrchestratorHintSegment[]) => segments.map(segment => segment.text).join('')

export const orchestratorContextHint = (newSelected: boolean) => hintText(orchestratorContextHintSegments(newSelected))

export const orchestratorGlobalHotkeyHint = () => hintText(orchestratorGlobalHotkeyHintSegments())

export const orchestratorHintSegmentColor = (t: Theme, role: OrchestratorHintRole) => {
  if (role === 'hotkey') {
    return t.color.accent
  }

  if (role === 'label') {
    return t.color.label
  }

  return t.color.muted
}

// Delegates to the shared list-row primitive so the session switcher and the
// completions popover cannot disagree about what "selected" looks like.
// (`selectionBg` remains the TEXT-selection highlight — a different semantic.)
export const selectedSessionRowStyle = (t: Theme) => ({
  backgroundColor: listRowStyle(t, true).backgroundColor,
  color: listRowStyle(t, true).color
})

export const newSessionMarkerColor = (t: Theme, selected: boolean) =>
  selected ? selectedSessionRowStyle(t).color : t.color.label

export const newSessionRowIndex = (sessionCount: number) => Math.max(0, sessionCount)

export const isNewSessionRow = (index: number, sessionCount: number) => index >= newSessionRowIndex(sessionCount)

export const canTypeOrchestratorPrompt = (index: number, sessionCount: number) => isNewSessionRow(index, sessionCount)

export const clampOrchestratorSelection = (index: number, sessionCount: number) =>
  Math.max(0, Math.min(index, newSessionRowIndex(sessionCount)))

export const currentSessionSelectionIndex = (
  sessions: readonly SessionActiveItem[],
  currentSessionId: null | string
) => {
  const index = sessions.findIndex(s => Boolean(s.current) || (!!currentSessionId && s.id === currentSessionId))

  return index >= 0 ? index : 0
}

export const orchestratorVisibleRowIndexes = (sessionCount: number, selected: number, visible = VISIBLE) => {
  const total = Math.max(0, sessionCount) + 1
  const clamped = clampOrchestratorSelection(selected, sessionCount)
  const offset = windowOffset(total, clamped, visible)
  const count = Math.min(visible, total - offset)

  return Array.from({ length: count }, (_, i) => offset + i)
}

export type CloseFallback = { action: 'activate'; sessionId: string } | { action: 'new' } | { action: 'stay' }

export const closeFallbackAfterClose = (
  closedId: string,
  currentSessionId: null | string,
  remaining: readonly SessionActiveItem[]
): CloseFallback => {
  if (!currentSessionId || closedId !== currentSessionId) {
    return { action: 'stay' }
  }

  const next = remaining.find(s => s.id !== closedId)

  return next ? { action: 'activate', sessionId: next.id } : { action: 'new' }
}

export const draftModelArgFromPickerValue = (value: string) => {
  return sessionScopedModelArg(value)
}

export const draftModelNameFromArg = (value: string) => {
  const parts = draftModelArgFromPickerValue(value).split(/\s+/).filter(Boolean)
  const modelParts: string[] = []

  for (let i = 0; i < parts.length; i++) {
    const part = parts[i]!

    if (part === '--provider' || part === '--reasoning') {
      i++

      continue
    }

    if (part.startsWith('--')) {
      continue
    }

    modelParts.push(part)
  }

  return modelParts.join(' ').trim()
}

export const draftModelDisplayLabel = (value: string) => {
  const modelName = draftModelNameFromArg(value)

  return modelName ? shortModel(modelName) : messages().pickers.session.currentOrDefault
}

export type OrchestratorRowClickAction = { action: 'activate'; sessionId: string } | { action: 'select-new' }

export const orchestratorRowClickAction = (
  index: number,
  sessions: readonly SessionActiveItem[]
): OrchestratorRowClickAction => {
  const target = sessions[index]

  return target && !isNewSessionRow(index, sessions.length)
    ? { action: 'activate', sessionId: target.id }
    : { action: 'select-new' }
}

export const draftTitleFromPrompt = (prompt: string, max = TITLE_MAX) => {
  const compact = prompt.replace(/\s+/g, ' ').trim()

  if (compact.length <= max) {
    return compact
  }

  return `${compact.slice(0, Math.max(0, max - 1)).trimEnd()}…`
}

function OrchestratorHintSegments({ segments, t }: OrchestratorHintTextProps) {
  return (
    <>
      {segments.map((segment, index) => (
        <Text color={orchestratorHintSegmentColor(t, segment.role)} key={`${segment.role}-${index}`}>
          {segment.text}
        </Text>
      ))}
    </>
  )
}

function OrchestratorHintText({ segments, t }: OrchestratorHintTextProps) {
  return (
    <Text color={orchestratorHintSegmentColor(t, 'text')} wrap="truncate-end">
      <OrchestratorHintSegments segments={segments} t={t} />
    </Text>
  )
}

export function ActiveSessionSwitcher({
  currentSessionId,
  gw,
  maxWidth,
  onCancel,
  onClose,
  onNew,
  onNewPrompt,
  onResume,
  onSelect,
  t
}: ActiveSessionSwitcherProps) {
  const [items, setItems] = useState<SessionActiveItem[]>([])
  const [history, setHistory] = useState<SessionListRow[]>([])
  const [err, setErr] = useState('')
  // A close/delete failure: kept apart from `err`, which every quiet poll rewrites.
  const [actionErr, setActionErr] = useState('')
  const [sel, setSel] = useState(0)
  const [loading, setLoading] = useState(true)
  const [draft, setDraft] = useState('')
  const [draftModel, setDraftModel] = useState('')
  const [pickingModel, setPickingModel] = useState(false)
  const [closingId, setClosingId] = useState('')
  // `/` filter: while open, printable keys feed the query and the row lists
  // narrow to fuzzy matches (client-side over the already-loaded pages).
  const [filtering, setFiltering] = useState(false)
  const [query, setQuery] = useState('')
  // Workspace pick on the `+new` row: Ctrl+O swaps the prompt input for a
  // path input; the confirmed value rides session.create as
  // `{ cwd, cwd_explicit: true }` (empty pick = default workspace resolution).
  // `cwdDraft` is the editor's working copy — it commits to `draftCwd` (the
  // pick) only on Enter, so Esc / Ctrl+O step back and discard the edit.
  const [draftCwd, setDraftCwd] = useState('')
  const [cwdDraft, setCwdDraft] = useState('')
  const [pickingWorkspace, setPickingWorkspace] = useState(false)
  // When non-null, the user pressed `d` on this (history) session and we await
  // a second `d` to confirm deletion. Tracked by session id (not row index) so
  // the 1.5s live-status poll re-indexing rows can't redirect the delete to a
  // different session. Any other key cancels the prompt.
  const [confirmDelete, setConfirmDelete] = useState<null | string>(null)
  // `w` narrows the list to the TUI's workspace (repo root, else directory); see `session.list` cwd.
  const [workspaceOnly, setWorkspaceOnly] = useState(false)
  const T = useT()
  const S = T.pickers.session
  const C = T.pickers.common
  const initialSelectionAppliedRef = useRef(false)
  // Holds the RAW `session.list` results (pre-dedupe). The quiet 1.5s poll
  // re-derives the resumable list from this against the latest live set, so a
  // session that was hidden while live reappears in history once it closes —
  // without re-querying the DB. Only refreshed on a full (includeHistory) load.
  const rawHistoryRef = useRef<SessionListRow[]>([])
  // The raw live list, same contract as `rawHistoryRef`: the displayed lists
  // are re-derived from these through the active filter on every poll/keystroke.
  const rawItemsRef = useRef<SessionActiveItem[]>([])
  // Mirror of `query` for the async load/derive paths.
  const queryRef = useRef('')
  // Mirror the displayed lists so the async poll can re-anchor the selection to
  // the *same* row (by session id) after live sessions appear/disappear, rather
  // than keeping a now-stale flat index.
  const itemsRef = useRef<SessionActiveItem[]>([])
  const historyDisplayRef = useRef<SessionListRow[]>([])
  const { stdout } = useStdout()
  // Optional maxWidth lets grid layouts hand the switcher its cell budget.
  const preferredWidth = Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, (stdout?.columns ?? 80) - 6))
  const width = clampOverlayWidth(preferredWidth, maxWidth)
  const promptColumns = Math.max(20, width - 11)

  // Rows are [new][live…][history…]: the "+ new" row is pinned first (index 0,
  // always rendered) and the live+history list is windowed below it. `total`
  // is the count of selectable rows (incl. the new row).
  const liveCount = items.length
  const histCount = history.length
  const listLen = liveCount + histCount
  const total = listLen + 1
  const rowKind = useCallback((index: number) => sessionRowKindAt(index, liveCount), [liveCount])

  // Derive the DISPLAYED lists from the raw rows through the active filter,
  // store them, and re-anchor the selection to the same row by identity (the
  // live list can grow/shrink between polls, which would otherwise drift a
  // flat index). Shared by network loads and keystroke-time refiltering.
  const commitLists = useCallback(
    (nextRaw: readonly SessionActiveItem[], histRaw: readonly SessionListRow[], initializeSelection: boolean) => {
      const hist = resumableHistory(histRaw, nextRaw)
      const { items: next, history: histFiltered } = filterSessionLists(nextRaw, hist, queryRef.current)
      const maxSel = next.length + histFiltered.length // == total - 1 (new row is index 0)

      setItems(next)
      setHistory(histFiltered)
      setSel(s => {
        if (initializeSelection) {
          // Land on the current live session (shifted +1 past the pinned new
          // row); with no live sessions, start on the new row itself.
          return next.length ? Math.min(currentSessionSelectionIndex(next, currentSessionId) + 1, maxSel) : 0
        }

        if (s <= 0) {
          return 0 // "+ new" row
        }

        const prevItems = itemsRef.current
        const prevHist = historyDisplayRef.current
        const clamp = () => Math.max(0, Math.min(s, maxSel))

        if (s - 1 < prevItems.length) {
          const id = prevItems[s - 1]?.id
          const i = id ? next.findIndex(x => x.id === id) : -1

          return i >= 0 ? i + 1 : clamp()
        }

        const id = prevHist[s - 1 - prevItems.length]?.id
        const i = id ? histFiltered.findIndex(x => x.id === id) : -1

        return i >= 0 ? 1 + next.length + i : clamp()
      })
    },
    [currentSessionId]
  )

  const load = useCallback(
    // `quiet` skips the loading spinner (used by the live-status poll);
    // `includeHistory` re-queries the resumable DB list (skipped on the 1.5s
    // poll, which only needs fresh live-session status).
    async (quiet = false, includeHistory = true, workspace = workspaceOnly) => {
      if (!quiet) {
        setLoading(true)
      }

      try {
        // Fetch independently (allSettled) so a failing session.list can't
        // wipe the live-session list: live sessions still render and the
        // resumable history degrades on its own.
        const canonicalList = gw.isCanonical
          ? gw.request<{ scope?: string; sessions?: (SessionListRow & { running?: boolean })[] }>('session.list', {
              limit: 200,
              ...sessionListWorkspaceParams(workspace, process.cwd())
            })
          : null

        const [liveRes, histRes] = await Promise.allSettled([
          canonicalList
            ? canonicalList.then(value => ({
                sessions: value.sessions?.map(row => ({
                  ...row,
                  current: row.id === currentSessionId,
                  status: row.running ? ('working' as const) : ('idle' as const)
                }))
              }))
            : gw.request<SessionActiveListResponse>('session.active_list', { current_session_id: currentSessionId }),
          includeHistory
            ? gw.request<SessionListResult>('session.list', {
                limit: 200,
                ...(canonicalList ? { scope: 'stored' } : {}),
                ...sessionListWorkspaceParams(workspace, process.cwd())
              })
            : Promise.resolve(null)
        ])

        const r = liveRes.status === 'fulfilled' ? asRpcResult<SessionActiveListResponse>(liveRes.value) : null

        if (!r) {
          setErr(messages().pickers.common.invalidResponse('session.active_list'))
          setLoading(false)

          return []
        }

        const nextRaw = r.sessions ?? []
        rawItemsRef.current = nextRaw

        // Surface a garbled/failed session.list rather than silently blanking
        // the resumable section; keep the last good raw history so a transient
        // failure doesn't wipe it.
        let histError = ''

        if (includeHistory) {
          if (histRes.status === 'fulfilled') {
            const parsedHist = asRpcResult<SessionListResult>(histRes.value)

            if (parsedHist) {
              rawHistoryRef.current = parsedHist.sessions ?? []
            } else {
              histError = messages().pickers.common.invalidResponse('session.list')
            }
          } else {
            histError = messages().pickers.session.errors.couldNotLoadResumable
          }
        }

        const initializeSelection = !initialSelectionAppliedRef.current
        initialSelectionAppliedRef.current = true
        commitLists(nextRaw, rawHistoryRef.current, initializeSelection)
        setErr(histError)
        setLoading(false)

        return nextRaw
      } catch (e: unknown) {
        setErr(rpcErrorMessage(e))
        setLoading(false)

        return []
      }
    },
    [commitLists, currentSessionId, gw, workspaceOnly]
  )

  useEffect(() => {
    itemsRef.current = items
    historyDisplayRef.current = history
  }, [items, history])

  // While a switcher input (filter, workspace pick) owns the keys, main must
  // not answer Esc with "close the overlay".
  useEffect(() => {
    $sessionsOverlayInputMode.set(filtering || pickingWorkspace)

    return () => {
      $sessionsOverlayInputMode.set(false)
    }
  }, [filtering, pickingWorkspace])

  // Keystroke-time refilter: re-derive the displayed lists from the raw rows
  // without a network round-trip; the selection re-anchors or clamps inside.
  const applyFilter = useCallback(
    (value: string) => {
      queryRef.current = value
      setQuery(value)
      commitLists(rawItemsRef.current, rawHistoryRef.current, false)
    },
    [commitLists]
  )

  useEffect(() => {
    void load()
    const timer = setInterval(() => void load(true, false), 1500)

    return () => clearInterval(timer)
  }, [load])

  const submitDraft = useCallback(
    (value: string) => {
      const prompt = value.trim()

      if (!prompt) {
        return
      }

      setDraft('')
      onNewPrompt(prompt, draftModel || undefined, draftCwd.trim() || undefined)
    },
    [draftCwd, draftModel, onNewPrompt]
  )

  const closeSelected = useCallback(async () => {
    const target = items[sel - 1]

    if (!target || rowKind(sel) !== 'live' || closingId) {
      return
    }

    setActionErr('')
    setClosingId(target.id)

    try {
      const result = await onClose(target.id)
      const closed = Boolean(result?.closed ?? result?.ok)

      if (!closed) {
        // A canonical view closes only what it holds: a null answer means no close was attempted, not
        // that main refused one. Only a receipt can say the session was already closed.
        setActionErr(
          gw.isCanonical && result === null
            ? messages().pickers.session.errors.notAttached
            : messages().pickers.session.errors.alreadyClosed
        )

        return
      }

      const remaining = await load(true)
      const fallback = closeFallbackAfterClose(target.id, currentSessionId, remaining)

      if (fallback.action === 'activate') {
        onSelect(fallback.sessionId)
      } else if (fallback.action === 'new') {
        onNew()
      } else {
        setSel(s => Math.max(0, Math.min(s, remaining.length + history.length)))
      }
    } catch (e: unknown) {
      setActionErr(rpcErrorMessage(e))
    } finally {
      setClosingId('')
    }
  }, [closingId, currentSessionId, gw.isCanonical, history.length, items, load, onClose, onNew, onSelect, rowKind, sel])

  const removeDeleted = useCallback(
    (id: string) => {
      rawHistoryRef.current = rawHistoryRef.current.filter(h => h.id !== id)
      setHistory(prev => prev.filter(h => h.id !== id))
      setSel(s => Math.max(0, Math.min(s, items.length + history.length - 1)))
    },
    [history.length, items.length]
  )

  const keepView = useCallback((resolved: string) => resolved === currentSessionId, [currentSessionId])
  const { deletePending, deleting, performDelete } = useSessionDelete(gw, removeDeleted, setActionErr, keepView)

  const handleRowClick = useCallback(
    (index: number) => (event: { stopImmediatePropagation?: () => void }) => {
      event.stopImmediatePropagation?.()

      // Keys are held while a delete is pending; a click must not open the row mid-delete either.
      if (deletePending()) {
        return
      }

      const kind = rowKind(index)
      const clamped = Math.max(0, Math.min(index, total - 1))

      if (kind === 'live') {
        setSel(clamped)
        onSelect(items[index - 1]!.id)

        return
      }

      if (kind === 'history') {
        setSel(clamped)
        onResume(history[index - 1 - items.length]!.id)

        return
      }

      setSel(0)
    },
    [deletePending, history, items, onResume, onSelect, rowKind, total]
  )

  const selectedKind = rowKind(sel)
  const newSelected = selectedKind === 'new'
  const draftHasText = Boolean(draft.trim())

  useInput((ch, key) => {
    if (pickingModel || deletePending()) {
      return
    }

    // Two-press history delete: once armed, only a second `d` deletes; any
    // other key cancels the prompt (mirrors the standalone resume picker).
    if (confirmDelete !== null) {
      if (ch?.toLowerCase() === 'd') {
        setConfirmDelete(null)
        performDelete(history.find(h => h.id === confirmDelete))
      } else {
        setConfirmDelete(null)
      }

      return
    }

    const lower = ch?.toLowerCase() ?? ''
    const isCtrl = (letter: string) => key.ctrl && (lower === letter || ch === ctrlChar(letter))

    // `/` filter mode: the input owns printable keys, so only navigation and
    // the two exits are handled here — every other switcher hotkey (d, w,
    // Ctrl+N/R/D, Tab) is suppressed until the filter closes. Esc clears the
    // query first, then leaves filter mode; the next Esc closes the overlay.
    if (filtering) {
      if (key.escape) {
        if (query.trim()) {
          applyFilter('')
        } else {
          setFiltering(false)
        }

        return
      }

      if (key.return) {
        setFiltering(false)

        if (newSelected) {
          if (!draftHasText) {
            onNew()
          }

          return
        }

        if (selectedKind === 'live' && items[sel - 1]) {
          return onSelect(items[sel - 1]!.id)
        }

        if (selectedKind === 'history' && history[sel - 1 - items.length]) {
          return onResume(history[sel - 1 - items.length]!.id)
        }

        return
      }

      if (key.upArrow && sel > 0) {
        return setSel(s => Math.max(0, s - 1))
      }

      if (key.downArrow && sel < total - 1) {
        return setSel(s => Math.min(total - 1, s + 1))
      }

      return
    }

    // Workspace pick: the input owns printable keys; Esc / Ctrl+O discard the
    // working copy and step back to the prompt, Enter commits it trimmed
    // (empty clears back to the default workspace resolution).
    if (pickingWorkspace) {
      if (key.escape || isCtrl('o')) {
        setPickingWorkspace(false)

        return
      }

      if (key.return) {
        setDraftCwd(cwdDraft.trim())
        setPickingWorkspace(false)

        return
      }

      return
    }

    if (key.escape) {
      return onCancel()
    }

    if (isCtrl('n')) {
      return onNew()
    }

    if (isCtrl('r')) {
      void load()

      return
    }

    if (key.tab) {
      if (newSelected) {
        setPickingModel(true)
      }

      return
    }

    if (isCtrl('o')) {
      if (newSelected) {
        if (pickingWorkspace) {
          setPickingWorkspace(false)
        } else {
          // Opening pre-fills the working copy from the stored pick; closing
          // discards the edit (the pick itself only moves on Enter).
          setCwdDraft(draftCwd)
          setPickingWorkspace(true)
        }
      }

      return
    }

    if (!key.ctrl && lower === 'w' && !newSelected) {
      const next = !workspaceOnly

      setWorkspaceOnly(next)
      void load(false, true, next)

      return
    }

    if (!key.ctrl && ch === '/' && !newSelected) {
      setFiltering(true)

      return
    }

    if (isCtrl('d')) {
      if (selectedKind === 'live') {
        void closeSelected()
      }

      return
    }

    // `d` arms deletion on a resumable history row. (On the New row `d` is
    // captured by the prompt's TextInput, so it never reaches here.)
    if (lower === 'd' && !key.ctrl && selectedKind === 'history') {
      setConfirmDelete(history[sel - 1 - items.length]?.id ?? null)

      return
    }

    if (newSelected && draftHasText) {
      return
    }

    if (key.upArrow && sel > 0) {
      return setSel(s => Math.max(0, s - 1))
    }

    if (key.downArrow && sel < total - 1) {
      return setSel(s => Math.min(total - 1, s + 1))
    }

    if (key.return) {
      if (newSelected) {
        if (!draftHasText) {
          return onNew(draftCwd.trim() || undefined)
        }

        return
      }

      if (selectedKind === 'live' && items[sel - 1]) {
        return onSelect(items[sel - 1]!.id)
      }

      if (selectedKind === 'history' && history[sel - 1 - items.length]) {
        return onResume(history[sel - 1 - items.length]!.id)
      }
    }
  })

  if (pickingModel) {
    return (
      <ModelPicker
        allowPersistGlobal={false}
        gw={gw}
        onCancel={() => setPickingModel(false)}
        onSelect={value => {
          setDraftModel(draftModelArgFromPickerValue(value))
          setPickingModel(false)
        }}
        sessionId={currentSessionId}
        t={t}
      />
    )
  }

  if (loading) {
    return <Text color={t.color.muted}>{S.loading}</Text>
  }

  // The "+ new" row (sel 0) is pinned at the top so it's always visible; the
  // live + history list is windowed beneath it.
  const listSel = sel > 0 ? sel - 1 : 0
  const offset = windowOffset(listLen, listSel, VISIBLE)
  const visibleCount = Math.max(0, Math.min(VISIBLE, listLen - offset))
  const visibleRows = Array.from({ length: visibleCount }, (_, k) => offset + k + 1)

  const newSelectedRow = sel === 0
  const newRowStyle = newSelectedRow ? selectedSessionRowStyle(t) : null
  const newRowTextColor = newRowStyle?.color
  const newRowMarkerColor = newSessionMarkerColor(t, newSelectedRow)
  const promptTitle = draftTitleFromPrompt(draft) || S.row.startNew

  return (
    <Box flexDirection="column" width={width}>
      <Text bold color={t.color.accent}>
        {S.title}
      </Text>
      <Text color={t.color.muted}>{sessionsCountLabel(items.length, history.length)}</Text>

      {(actionErr || err) && <Text color={t.color.label}>{C.error(actionErr || err)}</Text>}

      <Box backgroundColor={newRowStyle?.backgroundColor} flexDirection="row" onClick={handleRowClick(0)} width="100%">
        <Text bold={newSelectedRow} color={newRowTextColor ?? t.color.muted}>
          {newSelectedRow ? '▸ ' : '  '}
        </Text>

        <Box {...fixedSessionColumnStyle()} width={5}>
          <Text bold={newSelectedRow} color={newRowMarkerColor}>
            {'+'.padStart(2)}
          </Text>
        </Box>

        <Box {...fixedSessionColumnStyle()} width={11}>
          <Text bold={newSelectedRow} color={newRowMarkerColor} wrap="truncate-end">
            {S.row.new}
          </Text>
        </Box>

        <Box {...fixedSessionColumnStyle()} width={11}>
          <Text color={newRowTextColor ?? t.color.muted} wrap="truncate-end">
            {S.row.draft}
          </Text>
        </Box>

        <Box {...fixedSessionColumnStyle()} width={18}>
          <Text color={newRowTextColor ?? t.color.muted} wrap="truncate-end">
            {draftModelDisplayLabel(draftModel)}
          </Text>
        </Box>

        <Box flexGrow={1} flexShrink={1} minWidth={0}>
          <Text bold={newSelectedRow} color={newRowTextColor ?? t.color.muted} wrap="truncate-end">
            {promptTitle}
          </Text>
        </Box>
      </Box>

      {offset > 0 && <Text color={t.color.muted}>{C.moreAbove(offset)}</Text>}
      {!listLen && (
        <Text color={t.color.muted}>{query.trim() ? S.noMatches(query.trim()) : S.noOtherSessions}</Text>
      )}

      {visibleRows.map(i => {
        const selected = sel === i
        const selectedStyle = selected ? selectedSessionRowStyle(t) : null
        const rowTextColor = selectedStyle?.color
        const kind = rowKind(i)

        if (kind === 'history') {
          const h = history[i - 1 - items.length]!
          const pendingDelete = confirmDelete === h.id

          const title = pendingDelete
            ? S.row.pressDAgain
            : deleting && selected
              ? S.row.deleting
              : h.title || h.preview || S.row.untitled

          return (
            <Box
              backgroundColor={selectedStyle?.backgroundColor}
              flexDirection="row"
              key={h.id}
              onClick={handleRowClick(i)}
              width="100%"
            >
              <Text bold={selected} color={rowTextColor ?? t.color.muted}>
                {selected ? '▸ ' : '  '}
              </Text>

              <Box {...fixedSessionColumnStyle()} width={5}>
                <Text bold={selected} color={rowTextColor ?? t.color.muted}>
                  {String(i).padStart(2)}.
                </Text>
              </Box>

              <Box {...fixedSessionColumnStyle()} width={11}>
                <Text bold={selected} color={rowTextColor ?? t.color.muted} wrap="truncate-end">
                  {h.id}
                </Text>
              </Box>

              <Box {...fixedSessionColumnStyle()} width={11}>
                <Text color={rowTextColor ?? t.color.muted} wrap="truncate-end">
                  {relativeSessionAge(h.started_at)}
                </Text>
              </Box>

              <Box {...fixedSessionColumnStyle()} width={18}>
                <Text color={rowTextColor ?? t.color.muted} wrap="truncate-end">
                  {S.row.messageCount(h.message_count ?? 0)}
                </Text>
              </Box>

              <Box flexGrow={1} flexShrink={1} minWidth={0}>
                <Text
                  bold={selected}
                  color={pendingDelete ? t.color.label : (rowTextColor ?? t.color.muted)}
                  wrap="truncate-end"
                >
                  {title}
                </Text>
              </Box>
            </Box>
          )
        }

        const s = items[i - 1]!
        const status = s.status ?? 'idle'
        const current = s.current || s.id === currentSessionId
        const title = closingId === s.id ? S.row.closing : s.title || s.preview || S.row.untitled

        return (
          <Box
            backgroundColor={selectedStyle?.backgroundColor}
            flexDirection="row"
            key={s.id}
            onClick={handleRowClick(i)}
            width="100%"
          >
            <Text bold={selected} color={rowTextColor ?? t.color.muted}>
              {selected ? '▸ ' : '  '}
            </Text>

            <Box {...fixedSessionColumnStyle()} width={5}>
              <Text bold={selected} color={rowTextColor ?? t.color.muted}>
                {String(i).padStart(2)}.
              </Text>
            </Box>

            <Box {...fixedSessionColumnStyle()} width={11}>
              <Text
                bold={selected}
                color={rowTextColor ?? (current ? t.color.label : t.color.muted)}
                wrap="truncate-end"
              >
                {current ? S.row.current : s.id}
              </Text>
            </Box>

            <Box {...fixedSessionColumnStyle()} width={11}>
              <Text
                color={
                  rowTextColor ??
                  (status === 'working' ? t.color.ok : status === 'waiting' ? t.color.label : t.color.muted)
                }
                wrap="truncate-end"
              >
                {STATUS_GLYPH[status] ?? '·'} {sessionStatusLabel(status)}
              </Text>
            </Box>

            <Box {...fixedSessionColumnStyle()} width={18}>
              <Text color={rowTextColor ?? t.color.muted} wrap="truncate-end">
                {shortModel(s.model)}
              </Text>
            </Box>

            <Box flexGrow={1} flexShrink={1} minWidth={0}>
              <Text bold={selected} color={rowTextColor ?? t.color.muted} wrap="truncate-end">
                {title}
              </Text>
            </Box>
          </Box>
        )
      })}

      {offset + VISIBLE < listLen && <Text color={t.color.muted}>{C.moreBelow(listLen - offset - VISIBLE)}</Text>}

      {filtering ? (
        <>
          <Box marginTop={1}>
            <Text color={t.color.label}>{S.filterLabel}</Text>
            <TextInput
              color={t.color.text}
              columns={promptColumns}
              ignoreVerticalArrows
              onChange={applyFilter}
              value={query}
            />
          </Box>
          <OrchestratorHintText segments={orchestratorFilterHintSegments(selectedKind === 'history')} t={t} />
        </>
      ) : newSelected ? (
        <>
          <Box marginTop={1}>
            {pickingWorkspace ? (
              <>
                <Text color={t.color.label}>{S.workspaceLabel}</Text>
                <TextInput
                  color={t.color.text}
                  columns={promptColumns}
                  ignoreVerticalArrows
                  onChange={setCwdDraft}
                  value={cwdDraft}
                />
              </>
            ) : (
              <>
                <Text color={t.color.label}>{S.promptLabel}</Text>
                <TextInput
                  color={t.color.text}
                  columns={promptColumns}
                  onChange={setDraft}
                  onSubmit={submitDraft}
                  value={draft}
                />
              </>
            )}
          </Box>
          <OrchestratorHintText segments={orchestratorContextHintSegments(true)} t={t} />
          <Text color={t.color.muted} wrap="truncate-end">
            {S.draftModel(draftModelDisplayLabel(draftModel))}
          </Text>
          {draftCwd.trim() ? (
            <Text color={t.color.muted} wrap="truncate-end">
              {S.draftWorkspace(draftCwd.trim())}
            </Text>
          ) : null}
        </>
      ) : (
        <Box flexDirection="column" marginTop={1}>
          <OrchestratorHintText
            segments={
              selectedKind === 'history' ? resumeRowContextHintSegments() : orchestratorContextHintSegments(false)
            }
            t={t}
          />
          <Text color={t.color.muted} wrap="truncate-end">
            {S.selectNewPrefix}
            <Text color={newSessionMarkerColor(t, false)}>{S.newRowMarker}</Text>
            {S.selectNewSuffix}
          </Text>
        </Box>
      )}

      <OrchestratorHintText
        segments={[
          ...orchestratorGlobalHotkeyHintSegments(),
          ...(newSelected ? [] : workspaceHintSegments(workspaceOnly))
        ]}
        t={t}
      />
    </Box>
  )
}

interface OrchestratorHintTextProps {
  segments: readonly OrchestratorHintSegment[]
  t: Theme
}

interface ActiveSessionSwitcherProps {
  currentSessionId: null | string
  gw: GatewayClient
  maxWidth?: number
  onCancel: () => void
  onClose: (id: string) => Promise<null | SessionCloseResponse>
  onNew: (cwd?: string) => void
  onNewPrompt: (prompt: string, modelArg?: string, cwd?: string) => void
  onResume: (id: string) => void
  onSelect: (id: string) => void
  t: Theme
}
