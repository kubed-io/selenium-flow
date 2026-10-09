import { plural } from './format'
import type { SiteRow } from './types'

const sum = (r: SiteRow, k: 'local_storage' | 'session_storage') => r.storage.reduce((n, e) => n + e[k], 0)

/* One rule for every surface that lists saved sites: a cookie-only host reads
   "3 cookies"; one with storage gives every count. */
export const siteCounts = (r: SiteRow): string =>
  [
    plural(r.cookies, 'cookie'),
    ...(r.storage.length ? [sum(r, 'local_storage') + ' local', sum(r, 'session_storage') + ' session'] : []),
  ].join(' · ')
