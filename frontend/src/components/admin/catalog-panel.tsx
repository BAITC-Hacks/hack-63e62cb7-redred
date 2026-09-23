import { useState, type FormEvent } from "react"
import { RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import type { AdminRequest, SyncSettings, SyncStatus } from "@/lib/admin-api"
import { DataError, DataTable, Feedback, Loading, Metric, Panel, date, errorText, useAdminData } from "./admin-shared"

const statuses: Record<string, string> = { running: "Выполняется", scheduled: "По расписанию", disabled: "Выключен", not_configured: "Не настроен", succeeded: "Завершён", failed: "Ошибка", cancelled: "Остановлен", interrupted: "Прерван" }

export function CatalogPanel({ api }: { api: AdminRequest }) {
  const { data, error, refresh } = useAdminData<SyncStatus>(api, "/catalog/sync", 3000)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null)
  async function action(path: string, body?: unknown) {
    setBusy(true)
    setNotice(null)
    try { await api(path, "POST", body); setNotice({ text: body ? "Обновление запущено." : "Запрос на остановку принят.", error: false }); refresh() }
    catch (error) { setNotice({ text: errorText(error), error: true }) }
    finally { setBusy(false) }
  }
  const run = data?.current
  return <><DataError error={error} retry={refresh} />{notice && <Feedback error={notice.error}>{notice.text}</Feedback>}{!data ? !error && <Loading /> : <>
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"><Metric label="Товаров в базе" value={data.catalog_count.toLocaleString("ru-KZ")} /><Metric label="Последний успех" value={date(data.last_success?.finished_at)} /><Metric label="Следующий запуск" value={data.next_run_at ? date(data.next_run_at) : "Не назначен"} /><Metric label="Планировщик" value={statuses[data.scheduler_status] || data.scheduler_status} /></div>
    {!data.configured && <Feedback error>Учётные данные ЕКТ не настроены на сервере.</Feedback>}
    <Panel title="Синхронизация каталога"><Badge variant="secondary" className="gap-2">{run && <RefreshCw className="size-3 animate-spin" />}{run ? run.cancellation_requested ? "Остановка запрошена" : "Обновление выполняется" : "Нет активного обновления"}</Badge>
      <p className="text-sm leading-6 text-muted-foreground">{run ? `${run.mode === "full" ? "Полный обход" : "Обновление загруженных"} · ${run.trigger === "automatic" ? "по расписанию" : "вручную"} · ${date(run.started_at)}` : "Обновите карточки из базы или запустите полный обход ЕКТ, чтобы добавить новые товары."}</p>
      {run && <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">{[["Найдено", run.discovered_count], ["Обработано", run.processed_count], ["Обновлено", run.updated_count], ["Ошибок", run.failed_count], ["Страниц", run.pages_count]].map(([label, value]) => <div className="rounded-lg bg-zinc-50 p-3" key={label}><p className="text-xs text-muted-foreground">{label}</p><p className="mt-2 text-xl font-semibold">{value}</p></div>)}</div>}
      <div className="flex flex-wrap gap-2"><Button className="h-10" disabled={busy || !!run || !data.configured || !!error} onClick={() => void action("/catalog/sync", { mode: "existing" })}><RefreshCw />Обновить загруженные</Button><Button variant="outline" className="h-10" disabled={busy || !!run || !data.configured || !!error} onClick={() => void action("/catalog/sync", { mode: "full" })}>Полный обход каталога</Button>{run && <Button variant="destructive" className="h-10" disabled={busy || run.cancellation_requested} onClick={() => void action(`/catalog/sync/${run.id}/cancel`)}>Остановить</Button>}</div>
      <p className="text-xs text-muted-foreground">Полный обход может занять много времени. Прогресс обновляется автоматически.</p>
    </Panel>
    <Panel title="История запусков">{data.run_history.length ? <DataTable headings={["Начат", "Режим", "Итог", "Обработано", "Обновлено", "Ошибки"]}>{data.run_history.map(item => <tr key={item.id}><td>{date(item.started_at)}</td><td>{item.mode === "full" ? "Полный обход" : "Загруженные"}</td><td>{statuses[item.status] || item.status}{item.stop_reason && <p className="mt-1 text-xs text-muted-foreground">{item.stop_reason}</p>}</td><td>{item.processed_count}</td><td>{item.updated_count}</td><td className="max-w-60 break-words">{item.failed_count}{item.errors?.slice(0, 3).map((value, index) => <p key={index} className="mt-1 text-xs text-red-700">{typeof value === "string" ? value : JSON.stringify(value)}</p>)}</td></tr>)}</DataTable> : <p className="text-sm text-muted-foreground">Запусков пока нет.</p>}</Panel>
  </>}</>
}

export function SettingsPanel({ api }: { api: AdminRequest }) {
  const { data, error, refresh } = useAdminData<SyncSettings>(api, "/settings")
  return <><DataError error={error} retry={refresh} />{data ? <SettingsForm api={api} initial={data} /> : !error && <Loading />}</>
}

function SettingsForm({ api, initial }: { api: AdminRequest; initial: SyncSettings }) {
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null)
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    setBusy(true)
    setNotice(null)
    try {
      await api("/settings", "PATCH", { catalog_sync_enabled: form.has("enabled"), catalog_sync_interval_seconds: Number(form.get("interval")), catalog_sync_concurrency: Number(form.get("concurrency")), catalog_sync_timeout_seconds: Number(form.get("timeout")) })
      setNotice({ text: "Настройки сохранены и применены к планировщику.", error: false })
    } catch (error) { setNotice({ text: errorText(error), error: true }) }
    finally { setBusy(false) }
  }
  return <Panel title="Автоматическое обновление"><form onSubmit={save} className="max-w-2xl space-y-6"><fieldset disabled={busy} className="space-y-6"><label className="flex items-center gap-3 text-sm"><input type="checkbox" name="enabled" defaultChecked={initial.catalog_sync_enabled} className="size-4 accent-zinc-950" /> Обновлять каталог по расписанию</label><div className="grid gap-5 sm:grid-cols-2">
    <label className="space-y-2 text-sm"><span>Интервал, секунд</span><Input name="interval" className="mt-2 h-10" type="number" min={60} max={86400} step={1} required defaultValue={initial.catalog_sync_interval_seconds} /><span className="text-xs text-muted-foreground">После завершения предыдущего запуска.</span></label>
    <label className="space-y-2 text-sm"><span>Параллельных карточек</span><Input name="concurrency" className="mt-2 h-10" type="number" min={1} max={2} step={1} required defaultValue={initial.catalog_sync_concurrency} /></label>
    <label className="space-y-2 text-sm"><span>Таймаут запроса, секунд</span><Input name="timeout" className="mt-2 h-10" type="number" min={1} max={60} step={0.1} required defaultValue={initial.catalog_sync_timeout_seconds} /></label>
  </div><Button className="h-10">{busy ? "Сохраняем…" : "Сохранить настройки"}</Button></fieldset>{notice && <Feedback error={notice.error}>{notice.text}</Feedback>}</form></Panel>
}
