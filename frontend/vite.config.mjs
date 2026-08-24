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
        assetFileNames: 'styles[extname]',
      },
    },
  },
})
