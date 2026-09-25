import {resolve} from 'node:path'
import {fileURLToPath} from 'node:url'

import {astryxStylex} from '@astryxdesign/build/vite'
import react from '@vitejs/plugin-react'
import {defineConfig} from 'vite'

const here = fileURLToPath(new URL('.', import.meta.url))

// Built straight into the console's static dir. FastAPI serves it from there,
// so there is no second server to run and no CDN in the page.
const outDir = resolve(here, '../src/cufa/console/static/app')

export default defineConfig({
  // Where the browser will ask for these files. Without it, anything the CSS
  // references — the bundled font files — is emitted as a root-absolute
  // /assets/… URL, which is not where FastAPI serves them from, and the fonts
  // 404 silently: the page renders in whatever the system has and looks nearly
  // right, which is how it went unnoticed.
  base: '/static/app/',
  plugins: [...astryxStylex(), react()],
  optimizeDeps: {exclude: ['@astryxdesign/core', '@astryxdesign/theme-neutral']},
  build: {
    outDir,
    emptyOutDir: true,
    manifest: false,
    rollupOptions: {
      input: resolve(here, 'src/main.jsx'),
      output: {
        entryFileNames: 'console.js',
        // The stylesheet keeps a name the server can find by globbing the
        // output directory (see console.app._spa_assets). Everything else the
        // bundle pulls in — the two bundled font files — goes to assets/ under
        // its own name, rather than being handed the stylesheet's and taking a
        // numeric suffix to avoid the collision.
        assetFileNames: (asset) => {
          const name = asset.names?.[0] ?? asset.name ?? ''
          return name.endsWith('.css') ? 'styles[extname]' : 'assets/[name]-[hash][extname]'
        },
      },
    },
  },
})
