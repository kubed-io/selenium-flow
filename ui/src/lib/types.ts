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
  site_data_count?: number | null
  site_data_rev?: string | null
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

export interface Secret {
  name: string
  description?: string
  keys?: string[]
  restricted?: boolean
  allowed_urls?: string[]
  allowed_urls_rejected?: string | string[]
  origins?: { source: 'filesystem' | 'config'; location: string }[]
  key_sources?: Record<string, { from: 'filesystem' | 'file' | 'env' | 'value'; name?: string; path?: string }>
  keys_unresolved?: { key: string; reason: string }[]
  inline_keys?: string[]
}
export interface SecretsPayload {
  enabled: boolean
  count?: number
  secrets: Secret[]
}

export type SettingSource = 'default' | 'config' | 'env' | 'args'
export interface SettingRow {
  key: string
  name: string
  description: string
  value: unknown
  source: SettingSource
  sensitive?: boolean
  set?: boolean
}
export interface SettingsSection { name: string; description: string; settings: SettingRow[] }
export interface SettingsPayload { sections: SettingsSection[] }

export interface SiteSecret { name: string; description?: string; keys: string[] }
export interface SiteCookie {
  name: string
  value: string
  domain?: string
  path?: string
  expiry?: number | null
  http_only?: boolean
  secure?: boolean
  same_site?: string | null
  shared?: boolean
}
export interface SiteRow {
  site: string
  origin: string | null
  saved: boolean
  saved_at: number | null
  uri?: string
  cookies: number
  local_storage: number
  session_storage: number
  secrets: SiteSecret[]
}
/** A parent-domain cookie Forget leaves, named whole: two can share a name. */
export interface SharedCookie { name: string; domain: string; path: string }
export interface SiteDetail extends Omit<SiteRow, 'cookies' | 'local_storage' | 'session_storage'> {
  cookies: SiteCookie[]
  local_storage: Record<string, string>
  session_storage: Record<string, string>
  /** What Forget removes and what it leaves, by the server's own rule. */
  own_cookies: string[]
  kept_shared: SharedCookie[]
}
export interface SiteDataPayload {
  key: string
  sites: SiteRow[]
  saved_sites: number
  unleashed_secrets?: unknown
  uri?: string
  details: Record<string, SiteDetail>
}
