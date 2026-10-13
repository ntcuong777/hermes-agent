import React from 'react'
import { describe, expect, it } from 'vitest'

import { StatusRule } from '../components/appChrome.js'
import { DEFAULT_THEME } from '../theme.js'

const textContent = (node: React.ReactNode): string => {
  if (node === null || node === undefined || typeof node === 'boolean') {
    return ''
  }

  if (typeof node === 'string' || typeof node === 'number') {
    return String(node)
  }

  if (Array.isArray(node)) {
    return node.map(textContent).join('')
  }

  return React.isValidElement(node) ? textContent((node.props as { children?: React.ReactNode }).children) : ''
}

const baseProps = {
  bgCount: 0,
  busy: false,
  cols: 100,
  cwdLabel: '~/repo',
  liveSessionCount: 0,
  model: 'opus-4.8',
  sessionStartedAt: null,
  status: 'ready',
  statusColor: DEFAULT_THEME.color.ok,
  t: DEFAULT_THEME,
  turnStartedAt: null,
  usage: { context_max: 200_000, context_percent: 25, context_used: 50_000, total: 50_000 },
  voiceLabel: ''
}

// The person sets `agent.reasoning_effort: medium` and the bar must say so: hiding the middle level read
// as "no effort shown at all", so the session's effort could not be told from a model with none.
describe('StatusRule model label shows the session effort at every level', () => {
  it('shows medium', () => {
    expect(textContent(StatusRule({ ...baseProps, modelReasoningEffort: 'medium' }))).toContain('opus 4.8 medium')
  })

  it('still shows the other levels', () => {
    // every level Hermes accepts (VALID_REASONING_EFFORTS) plus `none`, which is how a disabled effort arrives
    for (const level of ['none', 'minimal', 'low', 'high', 'xhigh', 'max', 'ultra']) {
      expect(textContent(StatusRule({ ...baseProps, modelReasoningEffort: level }))).toContain(`opus 4.8 ${level}`)
    }
  })

  it('shows nothing when no effort is set or the route has no named level', () => {
    for (const level of [undefined, '', 'default', 'normal']) {
      const text = textContent(StatusRule({ ...baseProps, modelReasoningEffort: level }))

      expect(text).toContain('opus 4.8')
      expect(text).not.toMatch(/opus 4\.8 (medium|default|normal)/)
    }
  })
})
