import { useCallback, useEffect, useRef, useState } from "react"
import {
  ArrowUp,
  Bot,
  ExternalLink,
  FileText,
  Maximize2,
  MessageCircle,
  Minimize2,
  PackageSearch,
  Paperclip,
  RefreshCcw,
  ShoppingCart,
  X,
} from "lucide-react"

import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  ChatApiError,
  type ChatResponse,
  type Product,
  getAuthState,
  getChatHistory,
  resolveProposal,
  sendChatMessage,
} from "@/lib/chat-api"
import { createDemoResponse, resolveDemoProposal, demoEnabled } from "@/lib/demo-data"
import { cn } from "@/lib/utils"

type Message = {
  id: string
  role: "assistant" | "user"
  text: string
  fileName?: string
  response?: ChatResponse
  demo?: boolean
}

const welcomeMessage: Message = {
  id: "welcome",
  role: "assistant",
  text: "Здравствуйте! Помогу найти товар, проверить наличие по складам или подобрать аналог.",
}

const localHistoryKey = "ekt-chat-history-v1"

function readLocalHistory(): Message[] {
  try {
    const saved = JSON.parse(
      localStorage.getItem(localHistoryKey) ?? "[]"
    ) as unknown
    if (!Array.isArray(saved)) return [welcomeMessage]

    const messages = saved.filter(
      (item): item is Message =>
        typeof item === "object" &&
        item !== null &&
        typeof (item as Message).id === "string" &&
        typeof (item as Message).text === "string" &&
        ["assistant", "user"].includes((item as Message).role)
    )
    return messages.length ? messages.slice(-60) : [welcomeMessage]
  } catch {
    return [welcomeMessage]
  }
}

function saveLocalHistory(messages: Message[]) {
  try {
    localStorage.setItem(localHistoryKey, JSON.stringify(messages.slice(-60)))
  } catch {
    // The chat remains usable when storage is blocked or full.
  }
}

function formatMoney(value: string | null, currency = "KZT") {
  if (value === null) return "Цена по запросу"
  const amount = Number(value)
  if (!Number.isFinite(amount)) return `${value} ${currency}`
  return new Intl.NumberFormat("ru-KZ", {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(amount)
}

function ProductCard({ product }: { product: Product }) {
  const available = Number(product.available_quantity) > 0

  return (
    <Card className="gap-0 overflow-hidden py-0 shadow-none">
      <CardHeader className="grid grid-cols-[52px_1fr] gap-3 border-b px-3 py-3">
        <div className="flex size-13 items-center justify-center overflow-hidden rounded-lg bg-muted">
          {product.image_url ? (
            <img
              src={product.image_url}
              alt=""
              className="size-full object-contain p-1"
            />
          ) : (
            <PackageSearch className="size-5 text-muted-foreground" />
          )}
        </div>
        <div className="min-w-0">
          <CardTitle className="line-clamp-2 text-sm leading-5">
            {product.name}
          </CardTitle>
          <p className="mt-1 truncate text-xs text-muted-foreground">
            Арт. {product.article}
            {product.supplier_article ? ` · ${product.supplier_article}` : ""}
          </p>
          <p className="mt-2 text-base font-semibold">
            {formatMoney(product.price, product.currency)}
          </p>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 px-3 py-3">
        <Badge
          variant="outline"
          className={cn(
            "rounded-md",
            available
              ? "border-emerald-200 bg-emerald-50 text-emerald-700"
              : "border-red-200 bg-red-50 text-red-700"
          )}
        >
          {available
            ? `В наличии · ${product.available_quantity} шт.`
            : "Нет в наличии"}
        </Badge>

        {product.characteristics?.length ? (
          <dl className="grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
            {product.characteristics.slice(0, 4).map((item) => (
              <div key={item.code} className="min-w-0">
                <dt className="text-muted-foreground">{item.name}</dt>
                <dd className="mt-0.5 font-medium">{item.value}</dd>
              </div>
            ))}
          </dl>
        ) : null}

        {available && product.stores?.length ? (
          <div className="rounded-lg bg-muted/70 p-2.5 text-xs">
            <p className="mb-1.5 font-medium">Остатки по складам</p>
            <div className="space-y-1">
              {product.stores.slice(0, 3).map((store, index) => (
                <div
                  key={`${store.id ?? index}-${store.name}`}
                  className="flex justify-between gap-3"
                >
                  <span className="text-muted-foreground">{store.name}</span>
                  <span className="font-medium">{store.quantity} шт.</span>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {product.documents?.map((document) =>
          document.url ? (
            <a
              key={document.url}
              href={document.url}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-2 rounded-lg border p-2 text-xs transition-colors hover:bg-muted"
            >
              <FileText className="size-4 text-[#c2410c]" />
              <span className="min-w-0 flex-1 truncate">{document.title}</span>
              <ExternalLink className="size-3.5 text-muted-foreground" />
            </a>
          ) : (
            <div
              key={document.title}
              className="flex items-center gap-2 rounded-lg border border-dashed p-2 text-xs text-muted-foreground"
            >
              <FileText className="size-4 text-[#c2410c]" />
              <span>{document.title}</span>
            </div>
          )
        )}
      </CardContent>
      {product.product_url ? (
        <CardFooter className="border-t px-3 py-2">
          <Button variant="ghost" size="sm" asChild className="w-full">
            <a href={product.product_url} target="_blank" rel="noreferrer">
              Открыть карточку
              <ExternalLink data-icon="inline-end" />
            </a>
          </Button>
        </CardFooter>
      ) : null}
    </Card>
  )
}

function ProposalCard({
  response,
  busy,
  onResolve,
}: {
  response: ChatResponse
  busy: boolean
  onResolve: (action: "confirm" | "cancel") => void
}) {
  const proposal = response.proposal
  if (!proposal) return null

  if (proposal.status !== "pending") {
    return (
      <Alert>
        <ShoppingCart />
        <AlertDescription>
          {proposal.status === "confirmed"
            ? "Добавление подтверждено вами."
            : "Корзина не изменена."}
        </AlertDescription>
      </Alert>
    )
  }

  return (
    <Card className="gap-0 overflow-hidden border-[#c2410c] py-0 shadow-sm shadow-orange-950/5">
      <CardHeader className="border-b bg-orange-50/70 px-3 py-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="text-sm">Добавить в корзину?</CardTitle>
          <Badge className="bg-white text-[#c2410c]" variant="outline">
            Корзина не изменена
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 px-3 py-3 text-sm">
        {proposal.items.map((item) => (
          <div key={item.product_id} className="flex gap-3">
            <div className="min-w-0 flex-1">
              <p className="font-medium">{item.name}</p>
              <p className="text-xs text-muted-foreground">
                {item.quantity} ×{" "}
                {formatMoney(item.unit_price, proposal.currency)}
              </p>
            </div>
            <p className="font-medium whitespace-nowrap">
              {formatMoney(item.line_total, proposal.currency)}
            </p>
          </div>
        ))}
        <div className="flex justify-between border-t pt-2 font-semibold">
          <span>Итого</span>
          <span>{formatMoney(proposal.total, proposal.currency)}</span>
        </div>
      </CardContent>
      <CardFooter className="grid grid-cols-2 gap-2 border-t px-3 py-3">
        <Button
          className="bg-[#c2410c] hover:bg-[#9a3412]"
          disabled={busy}
          onClick={() => onResolve("confirm")}
        >
          Да, добавить
        </Button>
        <Button
          variant="outline"
          disabled={busy}
          onClick={() => onResolve("cancel")}
        >
          Нет
        </Button>
      </CardFooter>
    </Card>
  )
}

function MessageView({
  message,
  busy,
  onResolve,
}: {
  message: Message
  busy: boolean
  onResolve: (
    response: ChatResponse,
    action: "confirm" | "cancel",
    demo: boolean
  ) => void
}) {
  if (message.role === "user") {
    return (
      <div className="ml-auto max-w-[86%] rounded-xl bg-zinc-950 px-3 py-2.5 text-sm leading-5 text-white">
        {message.fileName ? (
          <span className="mb-1 flex items-center gap-1.5 text-xs text-zinc-300">
            <FileText className="size-3.5" />
            {message.fileName}
          </span>
        ) : null}
        {message.text}
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {message.demo ? (
        <Badge
          variant="outline"
          className="border-amber-200 bg-amber-50 text-amber-800"
        >
          Демо-ответ
        </Badge>
      ) : null}
      <div className="max-w-[92%] rounded-xl border bg-white px-3 py-2.5 text-sm leading-5 whitespace-pre-line">
        {message.text}
      </div>
      {message.response?.warnings.map((warning) => (
        <Alert key={warning} className="text-xs">
          <AlertDescription>{warning}</AlertDescription>
        </Alert>
      ))}
      {message.response?.products.map((product) => (
        <ProductCard key={product.id} product={product} />
      ))}
      {message.response ? (
        <ProposalCard
          response={message.response}
          busy={busy}
          onResolve={(action) =>
            onResolve(message.response!, action, Boolean(message.demo))
          }
        />
      ) : null}
      {message.response?.cart_url ? (
        <Button asChild className="w-full">
          <a href={`${import.meta.env.BASE_URL}cart`}>
            Перейти в корзину
            <ShoppingCart data-icon="inline-end" />
          </a>
        </Button>
      ) : null}
    </div>
  )
}

function canUseDemoFallback(error: unknown) {
  if (!demoEnabled) return false
  if (!(error instanceof ChatApiError)) return true
  return (
    !error.code ||
    [
      "ASSISTANT_UNAVAILABLE",
      "CATALOG_UNAVAILABLE",
      "DATABASE_UNAVAILABLE",
      "REQUEST_TIMEOUT",
    ].includes(error.code)
  )
}

export function ChatWidget() {
  const [open, setOpen] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [messages, setMessages] = useState<Message[]>(readLocalHistory)
  const [authenticated, setAuthenticated] = useState(false)
  const [input, setInput] = useState("")
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const endRef = useRef<HTMLDivElement>(null)
  const wasAuthenticated = useRef(false)

  useEffect(() => {
    if (!authenticated) saveLocalHistory(messages)
  }, [authenticated, messages])

  useEffect(() => {
    let active = true
    const syncHistory = async () => {
      try {
        const auth = await getAuthState()
        if (!active) return
        if (auth.authenticated) {
          const history = await getChatHistory()
          if (!active) return
          wasAuthenticated.current = true
          setAuthenticated(true)
          setMessages([
            welcomeMessage,
            ...history.items
              .filter((item) => item.status === "completed")
              .map((item): Message => ({
              id: item.id,
              role: item.role,
              text: item.text,
              response:
                item.role === "assistant" ? item.result ?? undefined : undefined,
              })),
          ])
        } else {
          setAuthenticated(false)
          if (wasAuthenticated.current) {
            wasAuthenticated.current = false
            setMessages(readLocalHistory())
          }
        }
      } catch {
        // Guest chat remains usable when the account service is unavailable.
      }
    }

    void syncHistory()
    window.addEventListener("auth-changed", syncHistory)
    return () => {
      active = false
      window.removeEventListener("auth-changed", syncHistory)
    }
  }, [])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages, busy])

  const reset = () => {
    localStorage.removeItem(localHistoryKey)
    setMessages([welcomeMessage])
    setInput("")
    setFile(null)
    setError(null)
  }

  const submit = useCallback(async (text = input, external = false) => {
    const trimmed = text.trim()
    const attachedFile = external ? null : file
    if ((!trimmed && !attachedFile) || busy) return

    const outgoing: Message = {
      id: crypto.randomUUID(),
      role: "user",
      text: trimmed || "Отправлено вложение",
      fileName: attachedFile?.name,
    }
    setMessages((current) => [...current, outgoing])
    if (!external) {
      setInput("")
      setFile(null)
    }
    setError(null)
    setBusy(true)

    const streamingId = crypto.randomUUID()
    try {
      const response = await sendChatMessage(trimmed, attachedFile, (delta) => {
        setMessages((current) => {
          const exists = current.some((item) => item.id === streamingId)
          return exists
            ? current.map((item) => item.id === streamingId ? { ...item, text: item.text + delta } : item)
            : [...current, { id: streamingId, role: "assistant", text: delta }]
        })
      })
      setMessages((current) => [
        ...current.filter((item) => item.id !== streamingId),
        {
          id: response.message_id,
          role: "assistant",
          text: response.text,
          response,
        },
      ])
    } catch (caught) {
      setMessages((current) => current.filter((item) => item.id !== streamingId))
      if (!canUseDemoFallback(caught)) {
        setError(
          caught instanceof ChatApiError
            ? caught.message
            : "Не удалось связаться с ассистентом."
        )
      } else {
        const response = await createDemoResponse(trimmed, attachedFile)
        setMessages((current) => [
          ...current,
          {
            id: response.message_id,
            role: "assistant",
            text: response.text,
            response,
            demo: true,
          },
        ])
      }
    } finally {
      setBusy(false)
    }
  }, [busy, file, input])

  useEffect(() => {
    const handleAssistantQuery = (event: Event) => {
      const prompt = (event as CustomEvent<string>).detail
      if (!prompt) return
      setOpen(true)
      if (busy) {
        setError("Дождитесь ответа ассистента и повторите запрос.")
        return
      }
      void submit(prompt, true)
    }

    window.addEventListener("assistant-query", handleAssistantQuery)
    return () =>
      window.removeEventListener("assistant-query", handleAssistantQuery)
  }, [busy, submit])

  const handleProposal = async (
    response: ChatResponse,
    action: "confirm" | "cancel",
    demo: boolean
  ) => {
    if (!response.proposal || busy) return
    setBusy(true)
    setError(null)

    try {
      const result = demo
        ? resolveDemoProposal(response.proposal, action)
        : await resolveProposal(response.proposal.id, action)
      const nextResponse: ChatResponse = demo
        ? (result as ChatResponse)
        : {
            message_id: crypto.randomUUID(),
            language: "ru",
            text:
              action === "confirm"
                ? "Корзина обновлена по вашему подтверждению."
                : "Добавление отменено. Корзина не изменена.",
            products: [],
            proposal: result.proposal ?? {
              ...response.proposal,
              status: action === "confirm" ? "confirmed" : "cancelled",
            },
            cart: result.cart ?? null,
            cart_url: result.cart_url ?? null,
            warnings: [],
          }

      const proposalId = response.proposal.id
      const resolvedStatus =
        nextResponse.proposal?.status ??
        (action === "confirm" ? "confirmed" : "cancelled")
      setMessages((current) => [
        ...current.map((message) => {
          if (
            !message.response?.proposal ||
            message.response.proposal.id !== proposalId
          ) {
            return message
          }

          return {
            ...message,
            response: {
              ...message.response,
              proposal: {
                ...message.response.proposal,
                status: resolvedStatus,
              },
            },
          }
        }),
        {
          id: nextResponse.message_id,
          role: "assistant",
          text: nextResponse.text,
          response: nextResponse,
          demo,
        },
      ])
      if (nextResponse.cart) window.dispatchEvent(new Event("cart-updated"))
    } catch (caught) {
      setError(
        caught instanceof ChatApiError
          ? caught.message
          : "Не удалось обработать подтверждение."
      )
    } finally {
      setBusy(false)
    }
  }

  if (!open) {
    return (
      <Button
        aria-label="Открыть ИИ-ассистента"
        size="lg"
        className="fixed right-4 bottom-[calc(1rem+env(safe-area-inset-bottom))] z-[60] h-12 cursor-pointer rounded-2xl bg-zinc-950 px-4 text-white shadow-xl hover:bg-zinc-800 md:right-6 md:bottom-6 md:h-14 md:px-5"
        onClick={() => setOpen(true)}
      >
        <MessageCircle data-icon="inline-start" />
        <span>Спросить ассистента</span>
      </Button>
    )
  }

  return (
    <>
      <Button
        type="button"
        aria-label="Закрыть окно чата нажатием на фон"
        className="fixed inset-0 z-50 h-auto w-full cursor-pointer rounded-none bg-black/45 p-0 hover:bg-black/45 md:hidden"
        onClick={() => setOpen(false)}
        variant="ghost"
      />
      <section
        aria-label="ИИ-ассистент ekt.kz"
        className={cn(
          "fixed z-[60] flex flex-col overflow-hidden border bg-white shadow-2xl transition-all duration-300",
          expanded
            ? "inset-0 h-dvh w-screen rounded-none"
            : "inset-x-0 bottom-0 h-[min(640px,calc(100dvh-44px))] rounded-t-[20px] md:inset-auto md:right-6 md:bottom-6 md:h-[min(660px,calc(100vh-48px))] md:w-[400px] md:rounded-2xl"
        )}
      >
        <header className="flex h-[72px] shrink-0 items-center gap-2 border-b px-4">
          <div className="relative flex size-10 shrink-0 items-center justify-center rounded-xl bg-[#c2410c] text-white">
            <Bot className="size-5" />
            <span className="absolute right-0 bottom-0 size-2.5 rounded-full border-2 border-white bg-emerald-500" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-sm font-semibold">Ассистент ekt.kz</h2>
            <p className="text-xs text-muted-foreground">
              Онлайн · отвечает за секунды
            </p>
          </div>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="icon" onClick={reset}>
                <RefreshCcw />
                <span className="sr-only">Начать диалог заново</span>
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom">Начать заново</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => setExpanded((value) => !value)}
              >
                {expanded ? <Minimize2 /> : <Maximize2 />}
                <span className="sr-only">
                  {expanded ? "Свернуть" : "Развернуть"}
                </span>
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom">
              {expanded ? "Свернуть" : "Развернуть"}
            </TooltipContent>
          </Tooltip>
          <Button variant="ghost" size="icon" onClick={() => setOpen(false)}>
            <X />
            <span className="sr-only">Закрыть чат</span>
          </Button>
        </header>

        <ScrollArea className="min-h-0 flex-1 bg-zinc-50/70">
          <div
            className={cn(
              "mx-auto flex w-full flex-col gap-3 p-4",
              expanded && "md:max-w-[760px]"
            )}
          >
            {messages.map((message) => (
              <MessageView
                key={message.id}
                message={message}
                busy={busy}
                onResolve={handleProposal}
              />
            ))}
            {busy ? (
              <div className="max-w-[82%] space-y-2 rounded-xl border bg-white p-3">
                <Skeleton className="h-3 w-36" />
                <Skeleton className="h-3 w-52 max-w-full" />
              </div>
            ) : null}
            {error ? (
              <Alert variant="destructive">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            ) : null}
            <div ref={endRef} />
          </div>
        </ScrollArea>

        <footer className="shrink-0 border-t bg-white p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
          <div
            className={cn("mx-auto space-y-2", expanded && "md:max-w-[760px]")}
          >
            {file ? (
              <div className="flex items-center gap-2 rounded-lg bg-muted px-2.5 py-2 text-xs">
                <FileText className="size-4 text-[#c2410c]" />
                <span className="min-w-0 flex-1 truncate">{file.name}</span>
                <Button
                  variant="ghost"
                  size="icon-xs"
                  onClick={() => setFile(null)}
                >
                  <X />
                  <span className="sr-only">Удалить вложение</span>
                </Button>
              </div>
            ) : null}
            <div className="flex items-end gap-2 rounded-xl border bg-white p-1.5 focus-within:ring-2 focus-within:ring-ring/40">
              <Input
                ref={fileInputRef}
                type="file"
                className="hidden"
                accept=".xlsx,.xls,.doc,.docx,.pdf,.jpg,.jpeg,image/jpeg,application/pdf"
                onChange={(event) => {
                  setFile(event.target.files?.[0] ?? null)
                  event.currentTarget.value = ""
                }}
              />
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="shrink-0"
                    onClick={() => fileInputRef.current?.click()}
                  >
                    <Paperclip />
                    <span className="sr-only">Прикрепить файл</span>
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Excel, Word, PDF или JPEG</TooltipContent>
              </Tooltip>
              <Textarea
                value={input}
                rows={1}
                placeholder="Артикул, название или вопрос"
                className="max-h-28 min-h-9 resize-none border-0 bg-transparent px-1 py-2 shadow-none focus-visible:ring-0"
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault()
                    void submit()
                  }
                }}
              />
              <Button
                size="icon"
                className="shrink-0"
                disabled={busy || (!input.trim() && !file)}
                onClick={() => void submit()}
              >
                <ArrowUp />
                <span className="sr-only">Отправить</span>
              </Button>
            </div>
          </div>
        </footer>
      </section>
    </>
  )
}
