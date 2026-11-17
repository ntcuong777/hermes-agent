#!/usr/bin/env node
/**
 * tui-slot-build: turn a slot copy (TSX that keeps upstream's own import lines) into the .mjs a plugin ships.
 *
 *   node scripts/fork/tui-slot-build.mjs --src <plugin>/tui/slots/x.tsx --out <plugin>/tui/dist/x.mjs --dir ui-tui/src/components
 *
 * `--dir` is the repo-relative directory of the UPSTREAM file the copy came from: it resolves the copy's relative
 * imports to host module ids (`../app/overlayStore.js` in ui-tui/src/components becomes `app/overlayStore`).
 * Every import of the copy becomes a lookup in the host module table the TUI publishes before it imports an
 * extension (see hostModules.ts); `import type` lines vanish; `node:` imports stay. The copy must end with
 *   export const sdkVersion = 1
 *   export const slots = { '<slot id>': <the copied component or function> }
 * and the build appends the `export default { sdkVersion, activate }` that registers them. JSX is compiled to
 * `createElement` calls of the host's React. Output is deterministic (same source, same bytes).
 */
import { readFileSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join, posix, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const IMPORT = /^import\s+([\s\S]*?)\s+from\s+['"]([^'"]+)['"][ \t]*;?[ \t]*(?:\/\/[^\n]*)?$/gm
const SIDE_EFFECT = /^import\s+['"][^'"]+['"][ \t]*;?[ \t]*$/m

/** The host module id of an import specifier seen from a file in `fileDir` (repo-relative, under ui-tui/src or apps). */
export const moduleId = (specifier, fileDir) => {
  if (specifier === 'react') return 'react'
  if (specifier === '@hermes/ink') return 'ink'
  if (specifier.startsWith('@hermes/shared/')) return `shared/${specifier.slice('@hermes/shared/'.length)}`

  if (specifier.startsWith('.')) {
    const base = fileDir.replace(/^ui-tui\/src\/?/, '')

    return posix.normalize(posix.join(base, specifier)).replace(/\.(js|jsx|ts|tsx)$/, '')
  }

  return `pkg/${specifier}`
}

const bindings = (clause, id) => {
  const text = clause.trim()
  const ns = text.match(/^\*\s+as\s+([\w$]+)$/)

  if (ns) return `const ${ns[1]} = __host('${id}')`

  const named = text.match(/^(?:([\w$]+)\s*,\s*)?\{([\s\S]*)\}$/)
  const def = named ? named[1] : /^[\w$]+$/.test(text) ? text : undefined

  const members = named
    ? named[2]
        .split(',')
        .map(part => part.trim())
        .filter(part => part && !/^type\s/.test(part))
        .map(part => part.replace(/\s+as\s+/, ': '))
    : []

  return [
    def ? `const ${def} = __host('${id}').default ?? __host('${id}')` : '',
    members.length ? `const { ${members.join(', ')} } = __host('${id}')` : ''
  ]
    .filter(Boolean)
    .join('\n')
}

/** `{ code, ids }`: the source with imports rewritten, and the host ids it now needs (sorted, unique). */
export const rewriteImports = (source, fileDir) => {
  if (SIDE_EFFECT.test(source)) {
    throw new Error('a slot copy cannot have a side-effect import (import "x")')
  }

  const ids = new Set()

  const code = source.replace(IMPORT, (whole, clause, specifier) => {
    if (/^type\s/.test(clause.trim()) || specifier.startsWith('node:')) {
      return /^type\s/.test(clause.trim()) ? '' : whole
    }

    const id = moduleId(specifier, fileDir)
    const out = bindings(clause, id)

    if (out) ids.add(id)

    return out
  })

  return { code, ids: [...ids].sort() }
}

const PRELUDE = `const __hostModules = globalThis[Symbol.for('hermes.tui.ext.host')] ?? {}
const __host = id => {
  const found = __hostModules[id]
  if (found === undefined) {
    throw new Error(\`slot copy needs host module '\${id}' (this TUI exposes: \${Object.keys(__hostModules).join(', ') || 'none'})\`)
  }
  return found
}
const __h = __host('react').createElement
const __Fragment = __host('react').Fragment
`

const EPILOGUE = `
export default {
  sdkVersion,
  activate(api) {
    for (const [id, impl] of Object.entries(slots)) api.slots.register(id, impl)
  }
}
`

export const buildSlotSource = async (source, fileDir, esbuild, loader = 'tsx') => {
  const { code, ids } = rewriteImports(source, fileDir)

  if (!/export\s+const\s+sdkVersion\b/.test(code) || !/export\s+const\s+slots\b/.test(code)) {
    throw new Error('the copy must export sdkVersion and slots (see the header of tui-slot-build.mjs)')
  }

  const out = await esbuild.transform(code, {
    format: 'esm',
    jsx: 'transform',
    jsxFactory: '__h',
    jsxFragment: '__Fragment',
    loader,
    target: 'node20'
  })

  return { code: `${PRELUDE}${out.code}${EPILOGUE}`, ids }
}

const loadEsbuild = forkRoot => createRequire(join(forkRoot, 'ui-tui', 'package.json'))('esbuild')

const arg = (name, argv) => {
  const at = argv.indexOf(name)

  return at >= 0 ? argv[at + 1] : undefined
}

const main = async argv => {
  const src = arg('--src', argv)
  const out = arg('--out', argv)
  const dir = arg('--dir', argv)
  const forkRoot = resolve(arg('--fork-root', argv) ?? join(dirname(fileURLToPath(import.meta.url)), '..', '..'))

  if (!src || !out || !dir) {
    console.error(
      'usage: tui-slot-build.mjs --src FILE.tsx --out FILE.mjs --dir REPO_RELATIVE_UPSTREAM_DIR [--fork-root DIR]'
    )

    return 2
  }

  // The source's extension decides the esbuild loader: a .tsx copy may hold JSX, a .ts copy may hold TS
  // generic arrows (`<T>(...)`), which the tsx loader would parse as a JSX start tag.
  const built = await buildSlotSource(readFileSync(src, 'utf8'), dir, loadEsbuild(forkRoot), src.endsWith('.tsx') ? 'tsx' : 'ts')

  writeFileSync(out, built.code)
  console.log(`built ${out} (${built.code.length} bytes); host modules: ${built.ids.join(', ') || 'none'}`)

  return 0
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = await main(process.argv.slice(2))
}
