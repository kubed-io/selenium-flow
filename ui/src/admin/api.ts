export type Api = <T = unknown>(path: string, method?: string, body?: unknown, signal?: AbortSignal) => Promise<T>

export function createApi(opts: { base: string; token: () => string; onUnauthorized: () => void }): Api {
  return async function api<T>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<T> {
    const res = await fetch(opts.base + path, {
      method,
      signal,
      headers: {
        Authorization: 'Bearer ' + opts.token(),
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    if (res.status === 401) { opts.onUnauthorized(); throw new Error('unauthorized') }
    if (!res.ok) {
      // The server's own message: it names the rule an operator hit.
      let said = ''
      try { said = (await res.json()).error || '' } catch { /* not JSON */ }
      throw new Error(said || 'request failed (' + res.status + ')')
    }
    return res.json() as Promise<T>
  }
}

export const sessionPath = (key: string, rest = ''): string =>
  '/admin/sessions/' + encodeURIComponent(key) + rest
