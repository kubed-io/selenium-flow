export type Api = <T = unknown>(path: string, method?: string, body?: unknown, signal?: AbortSignal) => Promise<T>

/* A refused call: the server's own message, and the status that says what kind of no. */
export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

/* `ready` runs before every call: a promise it returns is awaited, and may
   reject to send nothing; nothing returned sends at once. A 401
   asks `onUnauthorized` with the bearer it was sent; a yes is one more try with
   the bearer held then, whose own 401 is final and heard as `retried`. The
   server token's hook only ever says no; an OIDC sign-in renews first. */
export function createApi(opts: {
  base: string
  token: () => string
  onUnauthorized: (sent: string, retried: boolean) => boolean | void | Promise<boolean>
  ready?: () => Promise<void> | void
}): Api {
  return async function api<T>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<T> {
    const waiting = opts.ready?.()
    if (waiting) await waiting
    const send = (bearer: string) => fetch(opts.base + path, {
      method,
      signal,
      headers: {
        Authorization: 'Bearer ' + bearer,
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    const sent = opts.token()
    let res = await send(sent)
    if (res.status === 401 && (await opts.onUnauthorized(sent, false))) {
      const again = opts.token()
      res = await send(again)
      if (res.status === 401) await opts.onUnauthorized(again, true)
    }
    if (res.status === 401) throw new ApiError('unauthorized', 401)
    if (!res.ok) {
      // The server's own message: it names the rule an operator hit.
      let said = ''
      try { said = (await res.json()).error || '' } catch { /* not JSON */ }
      throw new ApiError(said || 'request failed (' + res.status + ')', res.status)
    }
    return res.json() as Promise<T>
  }
}

export const workspacePath = (key: string, rest = ''): string =>
  '/admin/workspaces/' + encodeURIComponent(key) + rest
