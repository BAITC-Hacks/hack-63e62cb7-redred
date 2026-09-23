import { useCallback, useEffect, useState, type FormEvent } from "react"
import { ArrowUpRight, Bot, LayoutDashboard, LogOut, MessageSquare, Package, Settings, ShieldCheck } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { AdminApiError, adminRequest, type AdminRequest, type AdminSession, type Dashboard } from "@/lib/admin-api"
import { DataError, Feedback, Loading, Metric, errorText, useAdminData } from "./admin-shared"
import { CatalogPanel, SettingsPanel } from "./catalog-panel"
import { ChatsPanel } from "./chats-panel"
import { AIPanel } from "./ai-panel"

const sections = [
  { id: "overview", title: "Обзор", icon: LayoutDashboard, description: "Клиенты, диалоги и каталог — всё в одном месте." },
  { id: "catalog", title: "Каталог", icon: Package, description: "Обновление товаров ЕКТ и история синхронизации." },
  { id: "chats", title: "Чаты", icon: MessageSquare, description: "Диалоги гостей и клиентов с ассистентом." },
  { id: "settings", title: "Настройки", icon: Settings, description: "Расписание и параметры обновления каталога." },
  { id: "ai", title: "AI-модель", icon: Bot, description: "Выбор, проверка и подключение модели ассистента." },
] as const
type Section = typeof sections[number]["id"]
function currentSection(): Section {
  const value = window.location.pathname.replace(/\/$/, "").split("/").pop()
  return sections.find(section => section.id === value)?.id ?? "overview"
}

export default function AdminPage() {
  const [section, setSection] = useState<Section>(currentSection)
  const [session, setSession] = useState<AdminSession | null>(null)
  const [checking, setChecking] = useState(true)
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const active = sections.find(item => item.id === section)!

  useEffect(() => {
    let mounted = true
    void adminRequest<AdminSession>("/session").then(value => {
      if (mounted) setSession(value)
    }).catch(error => {
      if (mounted && !(error instanceof AdminApiError && error.status === 401)) setError(errorText(error))
    }).finally(() => { if (mounted) setChecking(false) })
    const navigate = () => setSection(currentSection())
    window.addEventListener("popstate", navigate)
    return () => { mounted = false; window.removeEventListener("popstate", navigate) }
  }, [])

  useEffect(() => { document.title = `${active.title} · Администратор · ЭЛЕКТРОКОМПЛЕКТ` }, [active.title])

  const api: AdminRequest = useCallback(async <T,>(path: string, method = "GET", body?: unknown) => {
    try {
      return await adminRequest<T>(path, method, body, session?.csrf_token)
    } catch (error) {
      if (error instanceof AdminApiError && error.status === 401) {
        setSession(null)
        setError("Сессия истекла. Войдите снова.")
      }
      throw error
    }
  }, [session?.csrf_token])

  function navigate(next: Section) {
    window.history.pushState(null, "", `${import.meta.env.BASE_URL}admin${next === "overview" ? "" : `/${next}`}`)
    setSection(next)
  }

  async function login(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    const value = password
    setPassword("")
    try { setSession(await adminRequest<AdminSession>("/session", "POST", { password: value })) }
    catch (error) { setError(errorText(error)) }
    finally { setBusy(false) }
  }

  async function logout() {
    setBusy(true)
    setError(null)
    try { await api("/session", "DELETE"); setSession(null) }
    catch (error) { setError(errorText(error)) }
    finally { setBusy(false) }
  }

  return <main className="min-h-svh bg-zinc-100 p-0 md:p-6">
    <div className="mx-auto min-h-svh max-w-[1440px] overflow-hidden border bg-white shadow-sm md:min-h-[calc(100svh-48px)] md:rounded-xl">
      <header className="flex min-h-[70px] flex-wrap items-center justify-between gap-3 border-b px-4 py-4 md:px-8">
        <a href={import.meta.env.BASE_URL} className="flex flex-col"><span className="text-[15px] leading-4 font-black tracking-wide">ЭЛЕКТРОКОМПЛЕКТ</span><span className="mt-1 text-[10px] text-muted-foreground">ekt.kz · панель управления</span></a>
        <div className="flex items-center gap-2"><Badge variant="secondary" className="hidden gap-1.5 sm:inline-flex"><ShieldCheck className="size-3" /> Администратор</Badge><Button variant="ghost" asChild><a href={import.meta.env.BASE_URL}>В магазин <ArrowUpRight /></a></Button>{session && <Button variant="outline" onClick={() => void logout()} disabled={busy}><LogOut /> Выйти</Button>}</div>
      </header>
      {checking ? <div className="px-8"><Loading /></div> : !session ?
        <div className="mx-auto max-w-md px-6 py-16 md:py-24">
          <div className="mb-6 flex size-12 items-center justify-center rounded-xl bg-zinc-950 text-white"><ShieldCheck className="size-6" /></div>
          <h1 className="text-2xl font-semibold tracking-tight">Вход администратора</h1>
          <p className="mt-3 text-sm leading-6 text-muted-foreground">Управляйте каталогом, просматривайте диалоги и настраивайте ассистента.</p>
          <form onSubmit={login} className="mt-8 space-y-4"><label className="block space-y-2 text-sm font-medium"><span>Пароль администратора</span><Input className="h-11" type="password" autoComplete="current-password" maxLength={1024} required value={password} onChange={event => setPassword(event.target.value)} disabled={busy} /></label>{error && <Feedback error>{error}</Feedback>}<Button className="h-11 w-full" disabled={busy}>{busy ? "Входим…" : "Войти в панель"}</Button></form>
        </div> : <div className="flex flex-col lg:min-h-[calc(100svh-120px)] lg:flex-row">
          <aside className="shrink-0 border-b bg-zinc-50/70 p-3 lg:w-56 lg:border-r lg:border-b-0 lg:p-5"><p className="mb-4 hidden px-3 text-[10px] font-semibold tracking-[0.15em] text-muted-foreground uppercase lg:block">Рабочее пространство</p><nav aria-label="Разделы админки" className="flex gap-1 overflow-x-auto lg:flex-col">{sections.map(({ id, title, icon: Icon }) => <Button key={id} variant={section === id ? "default" : "ghost"} aria-current={section === id ? "page" : undefined} className="h-10 justify-start gap-3 px-3" onClick={() => navigate(id)}><Icon className="size-4" />{title}</Button>)}</nav><p className="mt-10 hidden px-3 text-xs leading-5 text-muted-foreground lg:block">ЭЛЕКТРОКОМПЛЕКТ<br />Управление сервисом</p></aside>
          <section className="min-w-0 flex-1 space-y-6 p-4 md:p-8"><div><p className="mb-2 text-xs text-muted-foreground">Панель управления / {active.title}</p><h1 className="text-2xl font-semibold tracking-tight">{active.title}</h1><p className="mt-2 text-sm text-muted-foreground">{active.description}</p></div>{error && <Feedback error>{error}</Feedback>}
            {section === "overview" && <Overview api={api} />}
            {section === "catalog" && <CatalogPanel api={api} />}
            {section === "chats" && <ChatsPanel api={api} />}
            {section === "settings" && <SettingsPanel api={api} />}
            {section === "ai" && <AIPanel api={api} />}
          </section>
        </div>}
    </div>
  </main>
}

function Overview({ api }: { api: AdminRequest }) {
  const { data, error, refresh } = useAdminData<Dashboard>(api, "/dashboard")
  const metrics = [["registered_clients", "Клиентов"], ["active_guests", "Активных гостей"], ["conversations", "Диалогов"], ["messages", "Сообщений"], ["catalog_products", "Товаров в каталоге"], ["cart_items", "Позиций в корзинах"], ["confirmed_proposals", "Подтверждённых предложений"]]
  return <><DataError error={error} retry={refresh} />{data ? <><div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{metrics.map(([key, label]) => <Metric key={key} label={label} value={data.counts[key].toLocaleString("ru-KZ")} />)}</div><p className="text-xs leading-5 text-muted-foreground">Показатели из базы приложения. Подтверждённое предложение означает добавление в корзину; оплаченные заказы и выручка здесь не учитываются.</p><Button variant="outline" onClick={refresh}>Обновить показатели</Button></> : !error && <Loading />}</>
}
