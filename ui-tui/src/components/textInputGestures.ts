import type { Key } from '@hermes/ink'

import { DEFAULT_VOICE_RECORD_KEY, isActionMod, isVoiceToggleKey, type ParsedVoiceRecordKey } from '../lib/platform.js'

export type RightClickDecision = { action: 'copy'; text: string } | { action: 'paste' }

/**
 * Decide what right-click should do on the composer:
 *   - non-empty selection → copy that text to the clipboard
 *   - no selection (or empty/collapsed range) → fall through to paste
 *
 * Mirrors terminal-native behavior (xterm, iTerm, gnome-terminal) where
 * right-click pastes only when there is nothing selected to copy.
 *
 * Callers pass the already-normalized range from `selRange()` (start <= end,
 * or null when collapsed), so this helper does not need to re-normalize.
 */
export function decideRightClickAction(
  value: string,
  range: { end: number; start: number } | null
): RightClickDecision {
  if (range && range.end > range.start) {
    const text = value.slice(range.start, range.end)

    if (text) {
      return { action: 'copy', text }
    }
  }

  return { action: 'paste' }
}

export const shouldPassThroughToGlobalHandler = (
  input: string,
  key: Key,
  voiceRecordKey: ParsedVoiceRecordKey = DEFAULT_VOICE_RECORD_KEY
): boolean =>
  (key.ctrl && input === 'c') ||
  (key.ctrl && input === 'x') ||
  (key.ctrl && input === 'o') ||
  (key.ctrl && (input === 'p' || input === 'r' || input === 't')) ||
  key.tab ||
  (key.shift && key.tab) ||
  key.pageUp ||
  key.pageDown ||
  key.escape ||
  isVoiceToggleKey(key, input, voiceRecordKey)

/**
 * The key gestures the composer treats as "paste from the clipboard": Alt+V
 * (ESC v), Ctrl+V, and Cmd/Ctrl+V via the platform action modifier. Ctrl+V is
 * matched on the parsed key, not the raw sequence: extended-key protocols
 * (kitty CSI u — tmux extended-keys, Ghostty, kitty, WezTerm) deliver it as
 * ESC [ 118 ; 5 u, and only the C0 byte \x16 arrives as `eventRaw`.
 */
export const isPasteHotkey = (
  eventRaw: string | undefined,
  input: string,
  key: Key,
  macHost: boolean
): boolean =>
  eventRaw === '\x1bv' ||
  eventRaw === '\x1bV' ||
  eventRaw === '\x16' ||
  (key.ctrl && input.toLowerCase() === 'v') ||
  (macHost && isActionMod(key) && input.toLowerCase() === 'v')
