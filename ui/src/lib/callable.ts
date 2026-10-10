/* The tools a view may call for itself. A view's `oncall` is a door to the
   server's tools from inside the app, so it opens onto this list and no wider. */
const CALLABLE = new Set(['network_body'])

type Result = { structuredContent?: unknown; isError?: boolean; content?: { type: string; text?: string }[] }

/* `oncall` for the views: a tool on the list, answered with its structured result. */
export const callable =
  (send: (p: { name: string; arguments: Record<string, unknown> }) => Promise<Result>) =>
  async (name: string, args: Record<string, unknown>): Promise<unknown> => {
    if (!CALLABLE.has(name)) throw new Error(`The app may not call ${name}.`)
    const r = await send({ name, arguments: args })
    if (r.isError) throw new Error(r.content?.find((c) => c.text)?.text || `Could not call ${name}.`)
    return r.structuredContent
  }
