/**
 * The "region" of an upstream source file that a slot copy was written against, and its hash.
 *
 * A region is the top-level declarations of the named symbols (doc comments included), with LF line endings and
 * trailing whitespace stripped. A top-level declaration starts at a line whose first character is not blank, not a
 * closer and not a comment; the doc-comment lines directly above it belong to it; it ends where the next one starts.
 * That is how Prettier lays out every file this fork patches.
 *
 * Plain JS with no imports: the TUI build (scripts/build/tui.mjs through slotHashes.mjs), the tests and the Python
 * `scripts/fork/tui-slot-check` (which re-implements it; slotRegion.vectors.json pins both) all use this one rule.
 */
import { createHash } from 'node:crypto'

export const ALGO = 'sha256-lf-region'

const DOC = line => line.startsWith('//') || line.startsWith('/*') || line.startsWith(' *') || line.startsWith('*/')
const START = line => line.length > 0 && !' \t})]'.includes(line[0]) && !DOC(line)
const escape = name => name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/** LF endings, trailing blanks stripped from every line, no trailing blank lines. */
export const normalize = text =>
  text
    .replace(/\r\n?/g, '\n')
    .split('\n')
    .map(line => line.replace(/[ \t]+$/, ''))
    .join('\n')
    .replace(/\n+$/, '')

/** `[{head, text}]`: every top-level statement of the file, `head` its first non-doc line. */
export const statements = source => {
  const lines = normalize(source).split('\n')
  const starts = lines.flatMap((line, index) => (START(line) ? [index] : []))

  const firsts = starts.map(start => {
    let first = start

    while (first > 0 && DOC(lines[first - 1])) {
      first -= 1
    }

    return first
  })

  return starts.map((start, k) => ({
    head: lines[start],
    text: lines
      .slice(firsts[k], k + 1 < firsts.length ? firsts[k + 1] : lines.length)
      .join('\n')
      .replace(/\n+$/, '')
  }))
}

const DECLARES = name =>
  new RegExp(
    `^(?:export\\s+)?(?:default\\s+)?(?:declare\\s+)?(?:async\\s+)?(?:function\\*?|const|let|var|class|interface|type|enum)\\s+${escape(name)}\\b`
  )

/** The region text of `symbols` (every statement declaring one, in the order of `symbols`). Throws when one is missing. */
export const regionText = (source, symbols) => {
  const all = statements(source)

  const parts = symbols.flatMap(name => {
    const found = all.filter(statement => DECLARES(name).test(statement.head))

    if (found.length === 0) {
      throw new Error(`symbol '${name}' not found among the top-level declarations`)
    }

    return found.map(statement => statement.text)
  })

  return parts.join('\n')
}

/** No symbols means the whole file (`sha256-lf`, the algorithm name spec-07 uses). */
export const regionHash = (source, symbols = []) =>
  createHash('sha256')
    .update(`${symbols.length > 0 ? regionText(source, symbols) : normalize(source)}\n`)
    .digest('hex')
