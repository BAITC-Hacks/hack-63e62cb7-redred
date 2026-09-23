import { useState, type FormEvent } from "react"
import { Search, MessageSquare } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import type { AdminChat, AdminRequest, Messages, Page } from "@/lib/admin-api"
import { DataError, DataTable, Loading, Pager, Panel, date, selectClass, useAdminData } from "./admin-shared"

export function ChatsPanel({ api }: { api: AdminRequest }) {
  const [filter, setFilter] = useState({ kind: "all", q: "", offset: 0 })
  const [selected, setSelected] = useState<string | null>(null)
  const params = new URLSearchParams({ ...filter, offset: String(filter.offset), limit: "20" })
  const { data, error, refresh } = useAdminData<Page<AdminChat>>(api, `/chats?${params}`)
  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const values = new FormData(event.currentTarget)
    setFilter({ kind: String(values.get("kind")), q: String(values.get("q")).trim(), offset: 0 })
    setSelected(null)
  }
  return <><Panel title="Диалоги"><form onSubmit={search} className="flex flex-wrap items-end gap-3"><label className="space-y-2 text-xs text-muted-foreground"><span>Тип пользователя</span><select name="kind" className={`${selectClass} mt-2`}><option value="all">Все пользователи</option><option value="client">Клиенты</option><option value="guest">Гости</option></select></label><label className="min-w-40 flex-1 space-y-2 text-xs text-muted-foreground"><span>Имя, email или ID</span><Input className="mt-2 h-10" type="search" name="q" maxLength={100} placeholder="Поиск по диалогам" /></label><Button className="h-10"><Search />Найти</Button></form>
    <DataError error={error} retry={refresh} />{data ? <>{data.items.length ? <DataTable headings={["Пользователь", "Тип", "Сообщений", "Последнее сообщение", ""]}>{data.items.map(item => <tr key={item.session_id} className={selected === item.session_id ? "bg-zinc-50" : ""}><td className="max-w-56 break-words"><p className="font-medium">{item.user?.name || item.user?.email || "Гость"}</p><p className="mt-1 text-xs text-muted-foreground">{item.user?.email || item.session_id}</p></td><td><Badge variant="secondary">{item.kind === "client" ? "Клиент" : "Гость"}</Badge></td><td>{item.message_count}</td><td>{date(item.last_message_at)}</td><td><Button variant="outline" onClick={() => setSelected(item.session_id)}>Открыть</Button></td></tr>)}</DataTable> : <div className="py-8 text-center text-sm text-muted-foreground"><MessageSquare className="mx-auto mb-3 size-6" />Чатов по этим условиям нет.</div>}<Pager offset={filter.offset} total={data.total} count={data.items.length} limit={20} onChange={offset => { setFilter({ ...filter, offset }); setSelected(null) }} /></> : !error && <Loading />}
  </Panel>{selected && <ChatMessages key={selected} api={api} id={selected} />}</>
}

function ChatMessages({ api, id }: { api: AdminRequest; id: string }) {
  const [offset, setOffset] = useState(0)
  const { data, error, refresh } = useAdminData<Messages>(api, `/chats/${encodeURIComponent(id)}/messages?limit=50&offset=${offset}`)
  return <Panel title={data?.session.user?.name || data?.session.user?.email || "Сообщения диалога"}><p className="text-xs break-all text-muted-foreground">{id} · Только просмотр</p><DataError error={error} retry={refresh} />{data ? <><div className="space-y-3">{data.items.map(message => <div key={message.id} className={`rounded-xl border p-4 ${message.role === "assistant" ? "bg-zinc-50" : "bg-white"}`}><p className="mb-2 text-xs text-muted-foreground">{message.role === "assistant" ? "Ассистент" : "Пользователь"} · {date(message.created_at)} · {message.status}{message.attachment_count > 0 && ` · вложений: ${message.attachment_count}`}</p><p className="text-sm leading-6 whitespace-pre-wrap break-words">{message.text}</p></div>)}{!data.items.length && <p className="text-sm text-muted-foreground">Сообщений нет.</p>}</div><Pager messages offset={offset} total={data.total} count={data.items.length} limit={50} onChange={setOffset} /></> : !error && <Loading />}</Panel>
}
