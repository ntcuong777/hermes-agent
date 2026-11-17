/**
 * The modules of the TUI a slot copy may import, by id. A copy keeps upstream's own import lines; the build tool
 * (scripts/fork/tui-slot-build.mjs) rewrites each to a lookup of the id below: `react`, `ink` for the two packages,
 * and the path relative to ui-tui/src without extension for a repo module (`app/overlayStore`, `lib/rpc`, ...).
 * A slot's `host` list in slots.json names the ids it needs; an id missing here fails that extension's import with
 * a message naming it. Adding an id is a minor SDK change; removing or re-typing one is a breaking change.
 *
 * Stage 0 exposes the two packages only. Each stage adds the repo modules its copies import.
 */
import * as ink from '@hermes/ink'
import * as React from 'react'
import * as slash from '@hermes/shared/slash' // fork: tui slot host modules (stage 2)
import * as slashInput from '@hermes/shared/slash-input' // fork: tui slot host modules (stage 2)
import * as uiStore from '../../app/uiStore.js' // fork: tui slot host modules (stage 2)
import * as i18nRuntime from '../../i18n/runtime.js' // fork: tui slot host modules (stage 2)

export const HOST_MODULES: Readonly<Record<string, unknown>> = Object.freeze({
  ink,
  react: React,
  'shared/slash': slash,
  'shared/slash-input': slashInput,
  'app/uiStore': uiStore,
  'i18n/runtime': i18nRuntime
})
