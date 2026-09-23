import { useCallback, useEffect, useState, type ReactNode } from "react"
import { LoaderCircle } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import type { AdminRequest } from "@/lib/admin-api"

export const date = (value?: string | null) => value ? new Date(value).toLocaleString("ru-KZ") : "—"
export const errorText = (error: unknown) => error instanceof Error ? error.message : "Не удалось выполнить запрос"
export const selectClass = "h-10 w-full min-w-0 rounded-lg border bg-white px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"

export function useAdminData<T>(api: AdminRequest, path: string, interval = 0) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)
  const refresh = useCallback(() => setRevision(value => value + 1), [])

  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined
    setData(null)
    setError(null)
    async function load() {
      try {
        const result = await api<T>(path)
        if (active) { setData(result); setError(null) }
      } catch (error) {
        if (active) setError(errorText(error))
      } finally {
        if (active && interval) timer = setTimeout(load, interval)
      }
    }
    void load()
    return () => { active = false; clearTimeout(timer) }
  }, [api, path, interval, revision])

  return { data, error, refresh }
}

export function Feedback({ children, error = false }: { children: ReactNode; error?: boolean }) {
  return <div role={error ? "alert" : "status"} className={`rounded-lg border px-4 py-3 text-sm ${error ? "border-red-200 bg-red-50 text-red-800" : "bg-zinc-50 text-zinc-700"}`}>{children}</div>
}

export function Loading() {
  return <div role="status" className="flex items-center gap-2 py-10 text-sm text-muted-foreground"><LoaderCircle className="size-4 animate-spin" /> Загрузка данных…</div>
}

export function DataError({ error, retry }: { error: string | null; retry: () => void }) {
  return error ? <Feedback error><div className="flex flex-wrap items-center justify-between gap-3"><span>{error}</span><Button variant="outline" onClick={retry}>Повторить</Button></div></Feedback> : null
}

export function Metric({ label, value }: { label: string; value: ReactNode }) {
  return <Card><CardContent className="space-y-3"><p className="text-xs text-muted-foreground">{label}</p><p className="text-xl font-semibold tracking-tight break-words md:text-2xl">{value}</p></CardContent></Card>
}

export function Panel({ title, children }: { title: string; children: ReactNode }) {
  return <Card><CardContent className="space-y-5"><h2 className="font-semibold">{title}</h2>{children}</CardContent></Card>
}

export function Pager({ offset, total, limit, count, onChange, messages = false }: { offset: number; total: number; limit: number; count: number; onChange: (offset: number) => void; messages?: boolean }) {
  return <div className="flex flex-wrap items-center justify-end gap-3 pt-4 text-xs text-muted-foreground">
    <span>{total ? `${offset + 1}–${offset + count} из ${total}` : "Нет записей"}</span>
    <Button variant="outline" disabled={!offset} onClick={() => onChange(Math.max(0, offset - limit))}>{messages ? "Новее" : "Назад"}</Button>
    <Button variant="outline" disabled={offset + count >= total} onClick={() => onChange(offset + limit)}>{messages ? "Старше" : "Далее"}</Button>
  </div>
}

export function DataTable({ headings, children }: { headings: string[]; children: ReactNode }) {
  return <div className="overflow-x-auto"><table className="w-full min-w-[600px] text-left text-sm [&_td]:border-b [&_td]:px-3 [&_td]:py-4 [&_td]:align-top [&_th]:px-3 [&_th]:py-3"><thead className="bg-zinc-50 text-xs text-muted-foreground"><tr>{headings.map((heading, index) => <th key={index} className="font-medium">{heading}</th>)}</tr></thead><tbody>{children}</tbody></table></div>
}
