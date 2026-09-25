import type { Api } from './api'
import type { SessionsPayload } from '../lib/types'

/* The session list is pushed, not polled. EventSource cannot send an
   Authorization header, so the URL it opens is signed and comes back from an
   authenticated call. */
export class Live {
  // Raw: replaced wholesale, and SessionDetail compares it by identity.
  data = $state.raw<SessionsPayload | null>(null)
  error = $state<string | null>(null)
  badge = $state({ text: 'connecting…', cls: '' })
  watching = $state(false)
  #events: EventSource | null = null
  #lastBeat = 0
  #fallback: ReturnType<typeof setInterval> | undefined
  #api: Api
  #root: string

  constructor(api: Api, root: string) {
    this.#api = api
    this.#root = root
  }

  async load(): Promise<SessionsPayload | undefined> {
    try {
      const data = await this.#api<SessionsPayload>('/admin/sessions')
      this.#apply(data)
      if (data.events_url) this.#watch(data.events_url)
      return data
    } catch (e) {
      this.error = (e as Error).message
    }
  }

  stop() {
    this.#events?.close()
    this.#events = null
    clearInterval(this.#fallback)
    this.watching = false
  }

  #apply(data: SessionsPayload) { this.data = data; this.error = null }

  #beat() { this.#lastBeat = Date.now(); this.badge = { text: 'live', cls: 'live' } }

  #watch(url: string) {
    if (this.#events) return
    const events = (this.#events = new EventSource(this.#root + url))
    this.watching = true
    events.onopen = () => this.#beat()
    events.onmessage = (e) => {
      this.#beat()
      try { this.#apply(JSON.parse(e.data)) } catch { /* a keepalive */ }
    }
    // EventSource reconnects by itself; the badge just stops claiming live.
    events.onerror = () => { this.badge = { text: 'reconnecting…', cls: '' } }

    // A stream swallowed by something in the middle would leave the page
    // silently wrong. A slow poll makes that failure invisible, not permanent.
    clearInterval(this.#fallback)
    this.#fallback = setInterval(async () => {
      if (Date.now() - this.#lastBeat < 45000) return
      this.badge = { text: 'polling', cls: '' }
      try {
        const data = await this.#api<SessionsPayload>('/admin/sessions')
        this.#apply(data)
        // The stream URL is signed and expires: a reconnect after that 401s
        // forever. This answer carries a freshly signed one.
        if (data.events_url) {
          this.#events?.close()
          this.#events = null
          this.#watch(data.events_url)
        }
      } catch { /* the next tick tries again */ }
    }, 30000)
  }
}
