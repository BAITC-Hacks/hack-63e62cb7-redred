import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import type { AICheck, AIModels, AIStatus, AdminRequest } from "@/lib/admin-api"
import { DataError, Feedback, Loading, Metric, Panel, date, errorText, selectClass, useAdminData } from "./admin-shared"

const healthNames: Record<string, string> = { healthy: "Работает", unknown: "Не проверено", fallback_pending: "Переход на базовую", unavailable: "Недоступна", not_configured: "Не настроен" }

export function AIPanel({ api }: { api: AdminRequest }) {
  const status = useAdminData<AIStatus>(api, "/ai/status", 5000)
  const models = useAdminData<AIModels>(api, "/ai/models")
  const [model, setModel] = useState("")
  const [proof, setProof] = useState<AICheck | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null)

  useEffect(() => {
    if (!proof) return
    const timer = setTimeout(() => { setProof(null); setNotice({ text: "Проверка устарела. Проверьте модель снова.", error: true }) }, Math.max(0, Date.parse(proof.expires_at) - Date.now()))
    return () => clearTimeout(timer)
  }, [proof])

  async function check() {
    setBusy(true); setProof(null); setNotice(null)
    try {
      const result = await api<AICheck>("/ai/check", "POST", { model })
      if (result.status !== "passed" || result.model !== model || !result.check_id || !(Date.parse(result.expires_at) > Date.now())) throw new Error("Проверка не завершилась успешно")
      setProof(result)
      setNotice({ text: `Проверка пройдена. Примените модель до ${date(result.expires_at)}.`, error: false })
      status.refresh()
    } catch (error) { setNotice({ text: errorText(error), error: true }) }
    finally { setBusy(false) }
  }

  async function apply() {
    if (!proof || proof.model !== model || Date.parse(proof.expires_at) <= Date.now()) return
    setBusy(true); setNotice(null)
    try { await api("/ai/activate", "POST", { model, check_id: proof.check_id }); setNotice({ text: "Активная модель обновлена.", error: false }); status.refresh() }
    catch (error) { setNotice({ text: errorText(error), error: true }) }
    finally { setProof(null); setBusy(false) }
  }

  const data = status.data
  return <><DataError error={status.error} retry={status.refresh} />{data ? <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"><Metric label="Активная модель" value={data.active_model} /><Metric label="По умолчанию" value={data.default_model} /><Metric label="Выбор администратора" value={data.override_model || "Нет"} /><Metric label="Состояние" value={healthNames[data.health] || data.health} /></div> : !status.error && <Loading />}
    <div className="grid gap-5 xl:grid-cols-2"><Panel title="Смена модели"><DataError error={models.error} retry={() => { setProof(null); models.refresh() }} /><label className="block space-y-2 text-sm"><span>Доступные модели</span><select className={selectClass} value={model} disabled={busy || !models.data} onChange={event => { setModel(event.target.value); setProof(null); setNotice(null) }}><option value="">{models.data ? "Выберите модель" : "Загрузка моделей…"}</option>{models.data?.models.map(value => <option key={value} value={value}>{value}</option>)}</select></label><p className="text-xs leading-5 text-muted-foreground">Проверка отправит короткий запрос в OpenAI. Модель изменится после успешной проверки и нажатия «Применить».</p><div className="flex flex-wrap gap-2"><Button variant="outline" disabled={busy} onClick={() => { setProof(null); models.refresh() }}>Обновить список</Button><Button disabled={busy || !model || !models.data || !!models.error} onClick={() => void check()}>{busy ? "Подождите…" : "Проверить"}</Button><Button disabled={busy || !proof || proof.model !== model || !!status.error} onClick={() => void apply()}>Применить</Button></div>{notice && <Feedback error={notice.error}>{notice.text}</Feedback>}{models.data && <p className="text-xs text-muted-foreground">Список получен: {date(models.data.checked_at)}</p>}</Panel>
    <Panel title="Последняя проверка"><p className="text-sm">Время: {date(data?.last_checked_at)}</p><p className="text-sm break-words">Последний сбой: {data ? data.last_incident ? `${data.last_incident.code} · ${date(data.last_incident.at)}${data.last_incident.fallback_to ? ` · переход на ${data.last_incident.fallback_to}` : ""}` : "Нет" : "—"}</p><p className="text-xs leading-5 text-muted-foreground">Если выбранная модель недоступна, сервер может использовать модель по умолчанию. Здесь отображается сохранённое состояние.</p></Panel></div>
  </>
}
