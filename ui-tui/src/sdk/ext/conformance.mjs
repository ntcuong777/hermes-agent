/**
 * Run the TESTS OF THE CORE COMPONENT against a slot copy: the strongest "nothing changed" proof there is.
 *
 *   cd <fork>/ui-tui && npx vitest run --config <plugin>/tui/vitest.conformance.config.mjs --maxWorkers=2 <the component's test files>
 *
 * with `<plugin>/tui/vitest.conformance.config.mjs` being
 *
 *   import { conformanceConfig } from `${process.env.HERMES_FORK}/ui-tui/src/sdk/ext/conformance.mjs`
 *   export default conformanceConfig({ copy: '<abs path of the copy .tsx>', dir: 'ui-tui/src/components',
 *                                      forkRoot: process.env.HERMES_FORK, setup: '<abs path of a setup file>', target: 'prompts' })
 *
 * Every import of `<target>.js` (from the tests and from other core modules) resolves to the copy, and the copy's own
 * imports (its upstream import lines, kept verbatim) resolve as if it lived at `<dir>` in the fork. The tests then
 * exercise the copy through the real modules. A test that fails here is a behaviour the copy does not have.
 */
import { realpathSync } from 'node:fs'
import { join } from 'node:path'

export const conformanceConfig = ({ copy: given, dir, forkRoot, setup, target }) => {
  // the real path: Vite hands importers over with symlinks resolved (macOS /var is /private/var)
  const copy = realpathSync(given)

  return {
    plugins: [
      {
        enforce: 'pre',
        name: 'slot-copy-imports',
        resolveId(source, importer, options) {
          return importer === copy
            ? this.resolve(source, join(forkRoot, dir, '__copy__.tsx'), { ...options, skipSelf: true })
            : null
        }
      }
    ],
    resolve: { alias: [{ find: new RegExp(`^\\.{1,2}/(?:[^\\s]*/)?${target}\\.js$`), replacement: copy }] },
    root: join(forkRoot, 'ui-tui'),
    // `setup`: an optional absolute path, run before each test file (the plugins pass the one that installs their seams).
    test: { exclude: ['dist/**', 'node_modules/**'], setupFiles: setup ? [setup] : [] }
  }
}
