export class AdminApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

export type AdminRequest = <T>(path: string, method?: string, body?: unknown) => Promise<T>

export async function adminRequest<T>(path: string, method = "GET", body?: unknown, csrf?: string): Promise<T> {
  const response = await fetch(`${import.meta.env.BASE_URL}api/admin${path}`, {
    method,
    credentials: "same-origin",
    cache: "no-store",
    headers: {
      Accept: "application/json",
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(method !== "GET" && csrf ? { "X-CSRF-Token": csrf } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    throw new AdminApiError(data?.error?.message || `Ошибка сервиса (${response.status})`, response.status)
  }
  if (data === null) throw new Error("Сервис вернул некорректный ответ")
  return data as T
}

export type AdminSession = { csrf_token: string }
export type Dashboard = { counts: Record<string, number> }
export type SyncRun = {
  id: string
  mode: string
  trigger: string
  status: string
  cancellation_requested: boolean
  started_at: string
  finished_at: string | null
  discovered_count: number
  processed_count: number
  updated_count: number
  failed_count: number
  pages_count: number
  stop_reason: string | null
  errors: unknown[]
}
export type SyncStatus = {
  configured: boolean
  scheduler_status: string
  catalog_count: number
  current: SyncRun | null
  last_success: SyncRun | null
  next_run_at: string | null
  run_history: SyncRun[]
}
export type SyncSettings = {
  catalog_sync_enabled: boolean
  catalog_sync_interval_seconds: number
  catalog_sync_concurrency: number
  catalog_sync_timeout_seconds: number
}
export type AdminChat = {
  session_id: string
  kind: "client" | "guest"
  user: { name: string | null; email: string } | null
  message_count: number
  last_message_at: string | null
}
export type AdminMessage = {
  id: string
  role: string
  text: string
  status: string
  attachment_count: number
  created_at: string
}
export type Page<T> = { items: T[]; total: number; limit: number; offset: number }
export type Messages = Page<AdminMessage> & { session: AdminChat }
export type AIStatus = {
  configured: boolean
  active_model: string
  default_model: string
  override_model: string | null
  health: string
  last_checked_at: string | null
  last_incident: { code: string; at: string; fallback_to?: string } | null
}
export type AIModels = { models: string[]; checked_at: string; cached: boolean }
export type AICheck = { check_id: string; model: string; status: string; expires_at: string }
