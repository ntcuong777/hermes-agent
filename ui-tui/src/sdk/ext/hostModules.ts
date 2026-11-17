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
import * as fuzzy from '@hermes/shared/fuzzy' // fork: tui slot host modules (stage 3)
import * as overlayStore from '../../app/overlayStore.js' // fork: tui slot host modules (stage 3)
import * as sessionDelete from '../../hooks/useSessionDelete.js' // fork: tui slot host modules (stage 3)
import * as useT from '../../i18n/useT.js' // fork: tui slot host modules (stage 3)
import * as rpc from '../../lib/rpc.js' // fork: tui slot host modules (stage 3)
import * as modelPicker from '../../components/modelPicker.js' // fork: tui slot host modules (stage 3)
import * as overlayControls from '../../components/overlayControls.js' // fork: tui slot host modules (stage 3)
import * as overlayPrimitives from '../../components/overlayPrimitives.js' // fork: tui slot host modules (stage 3)
import * as textInputModule from '../../components/textInput.js' // fork: tui slot host modules (stage 3)
import * as libText from '../../lib/text.js' // fork: tui slot host modules (stage 4)
import * as appTheme from '../../theme.js' // fork: tui slot host modules (stage 4)
import * as types from '../../types.js' // fork: tui slot host modules (stage 4)
import * as delegationStore from '../../app/delegationStore.js' // fork: tui slot host modules (stage 6)
import * as interfaces from '../../app/interfaces.js' // fork: tui slot host modules (stage 6)
import * as turnStore from '../../app/turnStore.js' // fork: tui slot host modules (stage 6)
import * as configEnv from '../../config/env.js' // fork: tui slot host modules (stage 6)
import * as faces from '../../content/faces.js' // fork: tui slot host modules (stage 6)
import * as verbs from '../../content/verbs.js' // fork: tui slot host modules (stage 6)
import * as domainMessages from '../../domain/messages.js' // fork: tui slot host modules (stage 6)
import * as domainViewport from '../../domain/viewport.js' // fork: tui slot host modules (stage 6)
import * as statusUsage from '../../lib/statusUsage.js' // fork: tui slot host modules (stage 6)
import * as subagentTree from '../../lib/subagentTree.js' // fork: tui slot host modules (stage 6)
import * as viewportStore from '../../lib/viewportStore.js' // fork: tui slot host modules (stage 6)
import * as sharedFormat from '@hermes/shared/format' // fork: tui slot host modules (stage 6)
import * as sharedGatewayEvents from '@hermes/shared/gateway-events' // fork: tui slot host modules (stage 6)
import * as nanostoresReact from '@nanostores/react' // fork: tui slot host modules (stage 6)
import * as unicodeAnimations from 'unicode-animations' // fork: tui slot host modules (stage 6)
import * as composerHighlights from '../../domain/composerHighlights.js' // fork: tui slot host modules (stage 5)
import * as inputMetrics from '../../lib/inputMetrics.js' // fork: tui slot host modules (stage 5)
import * as platform from '../../lib/platform.js' // fork: tui slot host modules (stage 5)
import * as termux from '../../lib/termux.js' // fork: tui slot host modules (stage 5)
import * as inputSelectionStore from '../../app/inputSelectionStore.js' // fork: tui slot host modules (stage 5)
import * as clipboard from '../../lib/clipboard.js' // fork: tui slot host modules (stage 5)
import * as configTiming from '../../config/timing.js' // fork: tui slot host modules (stage 5)
import * as precisionWheel from '../../lib/precisionWheel.js' // fork: tui slot host modules (stage 5)
import * as wheelAccel from '../../lib/wheelAccel.js' // fork: tui slot host modules (stage 5)
import * as sdkHost from '../../sdk/host.js' // fork: tui slot host modules (stage 5)
import * as agentRoster from '../../app/agentRoster.js' // fork: tui slot host modules (stage 5)
import * as backtrackStore from '../../app/backtrackStore.js' // fork: tui slot host modules (stage 5)
import * as keybinds from '../../app/keybinds.js' // fork: tui slot host modules (stage 5)
import * as pasteStore from '../../app/pasteStore.js' // fork: tui slot host modules (stage 5)
import * as serverRequestStore from '../../app/serverRequestStore.js' // fork: tui slot host modules (stage 5)
import * as stashCounts from '../../app/stashCounts.js' // fork: tui slot host modules (stage 5)
import * as turnController from '../../app/turnController.js' // fork: tui slot host modules (stage 5)
import * as gatewayCanonical from '@hermes/shared/gateway-canonical' // fork: tui slot host modules (stage 5)
import * as gatewayTypes from '@hermes/shared/gateway-types' // fork: tui slot host modules (stage 5)

export const HOST_MODULES: Readonly<Record<string, unknown>> = Object.freeze({
  ink,
  react: React,
  'shared/slash': slash,
  'shared/slash-input': slashInput,
  'app/uiStore': uiStore,
  'i18n/runtime': i18nRuntime,
  'shared/fuzzy': fuzzy,
  'app/overlayStore': overlayStore,
  'hooks/useSessionDelete': sessionDelete,
  'i18n/useT': useT,
  'lib/rpc': rpc,
  'components/modelPicker': modelPicker,
  'components/overlayControls': overlayControls,
  'components/overlayPrimitives': overlayPrimitives,
  'components/textInput': textInputModule,
  'lib/text': libText,
  appTheme: appTheme,
  types: types,
  'app/delegationStore': delegationStore,
  'app/interfaces': interfaces,
  'app/turnStore': turnStore,
  'config/env': configEnv,
  'content/faces': faces,
  'content/verbs': verbs,
  'domain/messages': domainMessages,
  'domain/viewport': domainViewport,
  'lib/statusUsage': statusUsage,
  'lib/subagentTree': subagentTree,
  'lib/viewportStore': viewportStore,
  'shared/format': sharedFormat,
  'shared/gateway-events': sharedGatewayEvents,
  'pkg/@nanostores/react': nanostoresReact,
  'pkg/unicode-animations': unicodeAnimations,
  'domain/composerHighlights': composerHighlights,
  'lib/inputMetrics': inputMetrics,
  'lib/platform': platform,
  'lib/termux': termux,
  'app/inputSelectionStore': inputSelectionStore,
  'lib/clipboard': clipboard,
  'config/timing': configTiming,
  'lib/precisionWheel': precisionWheel,
  'lib/wheelAccel': wheelAccel,
  'sdk/host': sdkHost,
  'app/agentRoster': agentRoster,
  'app/backtrackStore': backtrackStore,
  'app/keybinds': keybinds,
  'app/pasteStore': pasteStore,
  'app/serverRequestStore': serverRequestStore,
  'app/stashCounts': stashCounts,
  'app/turnController': turnController,
  'shared/gateway-canonical': gatewayCanonical,
  'shared/gateway-types': gatewayTypes
})
