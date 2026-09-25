import { gzipSync } from 'node:zlib'
import { readFileSync } from 'node:fs'

const OUT = new URL('../../kubed/selenium_flow/http/static/', import.meta.url)
const BUDGET = { admin: 45 * 1024, app: 110 * 1024 }
let over = false
for (const surface of /** @type {const} */ (['admin', 'app'])) {
  const bytes = ['html', 'css', 'js']
    .map((ext) => readFileSync(new URL(`${surface}.${ext}`, OUT)))
    .reduce((sum, b) => sum + gzipSync(b, { level: 9 }).length, 0)
  const ok = bytes <= BUDGET[surface]
  over ||= !ok
  console.log(`${surface}: ${(bytes / 1024).toFixed(1)} KB gzipped (budget ${BUDGET[surface] / 1024} KB)${ok ? '' : ' OVER'}`)
}
process.exit(over ? 1 : 0)
