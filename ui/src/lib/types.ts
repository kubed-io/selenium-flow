export type Folder = 'downloads' | 'screenshots' | 'files'

export interface FileEntry {
  name: string
  size: number
  url: string
  image?: boolean
  created?: number | null
  content_type?: string | null
}

export interface FilesData {
  downloads: FileEntry[]
  screenshots: FileEntry[]
  files: FileEntry[]
  browser: boolean
}

export interface Counts { downloads: number; screenshots: number; files: number }

export interface SessionRow {
  key: string
  name?: string | null
  owner?: string | null
  browser?: string | null
  version?: string | null
  session_id?: string | null
  live?: boolean
  attached?: boolean
  started?: number | null
  node?: string | null
  url?: string | null
  window?: string | null
  counts?: Counts | null
  files_count?: number | null
  files_rev?: string | number | null
  flows_rev?: string | number | null
}

export interface SessionsPayload { sessions: SessionRow[]; events_url?: string }

export interface FilesResponse {
  session?: SessionRow
  downloads?: FileEntry[]
  screenshots?: FileEntry[]
  files?: FileEntry[]
  browser?: boolean
}

export interface FlowSummary { name: string; step_count: number; shared?: boolean }
export interface FlowsListing { enabled: boolean; flows?: FlowSummary[]; session?: string; rev?: string | number | null }

/** A stored flow, as edited by a person: nothing about its shape is guaranteed (§F1.6). */
export interface FlowDoc {
  name: string
  shared?: boolean
  description?: string
  steps?: unknown
  parameters?: unknown
  uses?: unknown
  yaml?: string
}

export interface SecretUse { flow: string; steps: number[]; shared?: boolean; session?: string }
export interface Secret {
  name: string
  description?: string
  keys?: string[]
  restricted?: boolean
  allowed_urls?: string[]
  allowed_urls_rejected?: string | string[]
  source?: string
  location?: string
  uses?: SecretUse[]
}
export interface SecretsPayload {
  enabled: boolean
  secrets: Secret[]
  undefined: { name: string; uses: SecretUse[] }[]
}
