/* One panel's loads. Two can overlap for the same session — a slow answer for
   revision A landing after a fast one for B — so only the newest may paint, and
   the older one is aborted rather than left to finish for nothing. */
export class Latest {
  #controller: AbortController | null = null
  #seq = 0
  #current: Promise<void> = Promise.resolve()

  run<T>(load: (signal: AbortSignal) => Promise<T>, paint: (value: T) => void, fail?: (error: Error) => void): Promise<void> {
    this.#controller?.abort()
    const controller = (this.#controller = new AbortController())
    const mine = ++this.#seq
    const live = () => mine === this.#seq && !controller.signal.aborted
    const done = (async () => {
      try {
        const value = await load(controller.signal)
        if (live()) paint(value)
      } catch (error) {
        if (live() && fail) fail(error as Error)
      }
    })()
    this.#current = done
    return done
  }

  /* An overtaken load returns without painting, so awaiting it alone can hand
     back data from before what the caller just did. Wait for the newest. */
  async settled(): Promise<void> {
    let seen: number
    do { seen = this.#seq; await this.#current } while (seen !== this.#seq)
  }

  abort(): void {
    this.#controller?.abort()
    this.#seq++
  }
}
