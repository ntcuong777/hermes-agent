import { fileURLToPath } from 'node:url'

import { defineConfig } from 'vitest/config'

const src = fileURLToPath(new URL('./src/', import.meta.url))

export default defineConfig({
  resolve: {
    alias: {
      '@host/app/interfaces': `${src}app/interfaces.ts`,
      '@host/app/overlayStore': `${src}app/overlayStore.ts`,
      '@host/app/slashCatalog': `${src}app/slashCatalog.ts`,
      '@host/app/turnController': `${src}app/turnController.ts`,
      '@host/app/uiStore': `${src}app/uiStore.ts`,
      '@host/gatewayClient': `${src}gatewayClient.ts`,
      '@host/lib/rpc': `${src}lib/rpc.ts`,
      '@host/sdk/host': `${src}sdk/host.ts`,
      '@host/sdk/registry': `${src}sdk/registry.ts`,
      '@host/sdk/types': `${src}sdk/types.ts`
    }
  },
  test: {
    exclude: ['dist/**', 'node_modules/**'],
    setupFiles: ['./vitest.setup.ts']
  }
})
