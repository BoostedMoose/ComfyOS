/* Bundles the helper into one file: dist/bridge.mjs (run by dev/build_matter.sh, only when matter.js is updated).

   The bundle is what ComfyOS ships and what Node runs – locked in with `--permission --allow-fs-read=<this file>`,
   so there must be nothing left to load from node_modules at run time. Modules matter.js only uses on other
   runtimes (Bun's SQLite) are replaced by an empty stand-in; the helper never opens a database. */
import { build } from 'esbuild'
import { fileURLToPath } from 'node:url'

const here = fileURLToPath(new URL('.', import.meta.url))
const entry = process.argv[2] || 'bridge.mjs'
const outfile = process.argv[3] || 'dist/bridge.mjs'

const standIns = {
  name: 'stand-ins',
  setup (builder) {
    builder.onResolve({ filter: /^bun:/ }, () => ({ path: here + 'stubs/empty.mjs' }))
  }
}

const result = await build({
  absWorkingDir: here,
  entryPoints: [entry],
  outfile,
  bundle: true,
  platform: 'node',
  format: 'esm',
  target: 'node24',
  legalComments: 'none',
  charset: 'ascii',
  logLevel: 'warning',
  plugins: [standIns],
  metafile: true
})
const bytes = Object.values(result.metafile.outputs)[0].bytes
console.log(`${outfile}: ${(bytes / 1024 / 1024).toFixed(1)} MiB`)
