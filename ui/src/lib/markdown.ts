import { Lexer, type Token, type Tokens } from 'marked'

/* YAML front matter is a skill's metadata, not its text; lexed, it becomes a
   rule and a setext heading. */
export function body(text: string): string {
  if (!text.startsWith('---\n')) return text
  const end = text.indexOf('\n---\n', 4)
  return end === -1 ? text : text.slice(end + 5)
}

export interface Doc { title: Tokens.Heading | null; blocks: Token[] }

/* A document's blocks, its first # heading lifted out as the title. Tokens
   only: the app draws them with Svelte and never renders an HTML string. */
export function parse(text: string): Doc {
  const tokens: Token[] = new Lexer({ gfm: true }).lex(body(text)).filter((t) => t.type !== 'space' && t.type !== 'def')
  const at = tokens.findIndex((t) => t.type === 'heading' && (t as Tokens.Heading).depth === 1)
  if (at === -1) return { title: null, blocks: tokens }
  return { title: tokens[at] as Tokens.Heading, blocks: tokens.filter((_, i) => i !== at) }
}
