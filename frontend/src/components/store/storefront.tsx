import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react"
import {
  Check,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  FileText,
  Menu,
  Minus,
  Package,
  Plus,
  Search,
  ShoppingCart,
  SlidersHorizontal,
  Trash2,
  UserRound,
} from "lucide-react"

import { AuthDialog } from "@/components/auth/auth-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import {
  getCart,
  getProduct,
  getProductFacets,
  prepareCartProposal,
  removeCartItem,
  resolveProposal,
  searchProducts,
  type CatalogFilters,
  type CatalogFacets,
  type CartProposal,
  type ChatApiError,
  type Cart,
  type Product,
} from "@/lib/chat-api"
import {
  demoCatalog,
  demoEnabled,
  prepareDemoProposal,
  readDemoCart,
  removeDemoCartItem,
  resolveDemoProposal,
} from "@/lib/demo-data"

type Page =
  { name: "catalog" } | { name: "product"; product: Product } | { name: "cart" }

type CartAction = {
  product: Product
  quantity: number
  proposal: CartProposal | null
  demo: boolean
  status: "preparing" | "ready" | "adding" | "done" | "error"
  error: string | null
}

type Filters = {
  available: boolean
  hasDocuments: boolean | null
  hasCertificates: boolean | null
  series: string[]
  currents: string[]
  capacities: string[]
}

const emptyFilters: Filters = {
  available: false,
  hasDocuments: null,
  hasCertificates: null,
  series: [],
  currents: [],
  capacities: [],
}

const presenceOptions = [
  { value: null, label: "Все" },
  { value: true, label: "Есть" },
  { value: false, label: "Нет" },
] as const

const numericOptions = (values: string[]) =>
  [...new Set(values)].sort(
    (a, b) =>
      Number.parseFloat(a.replace(",", ".")) -
      Number.parseFloat(b.replace(",", "."))
  )

const demoFacets: CatalogFacets = {
  categories: [...new Set(demoCatalog.map((product) => product.category).filter((value): value is string => Boolean(value)))],
  series: [...new Set(demoCatalog.map((product) => product.series))],
  current: numericOptions(demoCatalog.map((product) => `${product.current} А`)),
  breaking_capacity: numericOptions(
    demoCatalog.map((product) => `${product.breakingCapacity} кА`)
  ),
}

const catalogPageSize = 20

function paginationItems(page: number, pageCount: number) {
  const pages = [...new Set([1, page - 1, page, page + 1, pageCount])]
    .filter((value) => value >= 1 && value <= pageCount)
    .sort((a, b) => a - b)
  const items: Array<number | "…"> = []
  for (const value of pages) {
    const previous = items.at(-1)
    if (typeof previous === "number" && value - previous === 2) {
      items.push(previous + 1)
    } else if (typeof previous === "number" && value - previous > 2) {
      items.push("…")
    }
    items.push(value)
  }
  return items
}

const currency = new Intl.NumberFormat("ru-KZ", {
  maximumFractionDigits: 0,
  style: "currency",
  currency: "KZT",
})

function formatPrice(value: string | null | undefined) {
  return value == null ? "Цена по запросу" : currency.format(Number(value))
}

function documentHref(value: string) {
  try {
    const url = new URL(value)
    return url.protocol === "https:" ? url.href : null
  } catch {
    return null
  }
}

function productCount(count: number) {
  const last = count % 10
  const lastTwo = count % 100
  const noun =
    last === 1 && lastTwo !== 11
      ? "товар"
      : last >= 2 && last <= 4 && (lastTwo < 12 || lastTwo > 14)
        ? "товара"
        : "товаров"
  return `${count} ${noun}`
}

function askAssistant(prompt: string) {
  window.dispatchEvent(new CustomEvent("assistant-query", { detail: prompt }))
}

function Header({
  cartCount,
  onAuth,
  onCart,
  onHome,
}: {
  cartCount: number
  onAuth: () => void
  onCart: () => void
  onHome: () => void
}) {
  const [query, setQuery] = useState("")

  const submit = (event: FormEvent) => {
    event.preventDefault()
    window.dispatchEvent(new CustomEvent("catalog-search", { detail: query }))
  }

  return (
    <header className="border-b bg-white">
      <div className="flex h-[70px] items-center gap-4 px-4 md:px-10">
        <Button
          className="mr-auto h-auto shrink-0 p-0 text-left hover:bg-transparent md:mr-0"
          onClick={onHome}
          type="button"
          variant="ghost"
        >
          <span className="block text-[15px] leading-4 font-black tracking-wide">
            ЭЛЕКТРОКОМПЛЕКТ
          </span>
          <span className="block text-[10px] text-muted-foreground">
            ekt.kz
          </span>
        </Button>
        <Button
          className="hidden h-10 bg-zinc-950 px-4 md:inline-flex"
          onClick={onHome}
        >
          <Menu className="size-4" /> Каталог
        </Button>
        <form className="hidden flex-1 md:block" onSubmit={submit}>
          <div className="relative">
            <Search className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="h-10 pl-10"
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Название, артикул или код производителя"
              value={query}
            />
          </div>
        </form>
        <Button
          aria-label="Войти в личный кабинет"
          onClick={onAuth}
          size="icon"
          variant="ghost"
        >
          <UserRound className="size-5" />
        </Button>
        <Button
          className="relative h-10 px-3 md:px-4"
          onClick={onCart}
          variant="outline"
        >
          <ShoppingCart className="size-5" />
          <span className="hidden md:inline">Корзина</span>
          <span className="grid min-w-5 place-items-center rounded-full bg-zinc-100 px-1 text-[11px]">
            {cartCount}
          </span>
        </Button>
      </div>
      <form className="px-4 pb-3 md:hidden" onSubmit={submit}>
        <div className="relative">
          <Search className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            className="h-10 pl-10"
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Название или артикул"
            value={query}
          />
        </div>
      </form>
    </header>
  )
}

function ProductImage({
  product,
  detail = false,
}: {
  product: Product
  detail?: boolean
}) {
  return (
    <div
      className={`relative w-full overflow-hidden bg-white ${detail ? "h-[360px] rounded-xl border" : "h-[168px] md:h-[170px]"}`}
    >
      {product.image_url ? (
        <img
          className={`absolute inset-0 size-full object-contain transition-transform duration-200 group-hover:scale-[1.04] ${detail ? "p-8" : "p-2 md:p-3"}`}
          src={product.image_url}
          alt=""
          decoding="async"
          loading={detail ? "eager" : "lazy"}
        />
      ) : (
        <Package className="absolute top-1/2 left-1/2 size-12 -translate-x-1/2 -translate-y-1/2 text-zinc-200" />
      )}
    </div>
  )
}

function ProductTile({
  product,
  onSelect,
  onAdd,
}: {
  product: Product
  onSelect: (product: Product) => void
  onAdd: (product: Product, quantity: number) => void
}) {
  const available = Number(product.available_quantity) > 0
  return (
    <article className="flex min-w-0 flex-col overflow-hidden rounded-xl border bg-white">
      <Button
        aria-label={`Открыть ${product.name}`}
        className="group h-auto w-full cursor-pointer rounded-none p-0 hover:bg-zinc-50"
        onClick={() => onSelect(product)}
        type="button"
        variant="ghost"
      >
        <ProductImage product={product} />
      </Button>
      <div className="flex flex-1 flex-col border-t px-3 py-3">
        <p className="text-[11px] text-muted-foreground">{product.article}</p>
        <Button
          className="mt-1 h-auto min-h-[48px] cursor-pointer justify-start p-0 text-left text-[13px] leading-[18px] font-medium whitespace-normal hover:bg-transparent hover:underline"
          onClick={() => onSelect(product)}
          type="button"
          variant="ghost"
        >
          {product.name}
        </Button>
        <p
          className={`mt-1 text-[12px] font-medium ${available ? "text-emerald-600" : "text-muted-foreground"}`}
        >
          {available
            ? `В наличии ${product.available_quantity} шт.`
            : "Нет в наличии"}
        </p>
        <div className="mt-auto flex flex-col items-stretch gap-2 pt-3 md:flex-row md:items-center md:justify-between">
          <strong className="text-[17px] whitespace-nowrap">
            {formatPrice(product.price)}
          </strong>
          <Button
            className={
              available
                ? "h-9 bg-[#c2410c] px-3 hover:bg-[#9a3412]"
                : "h-9 px-3"
            }
            onClick={() =>
              available
                ? onAdd(product, 1)
                : askAssistant(
                    `Найди аналог для ${product.supplier_article ?? product.article}, которого нет в наличии`
                  )
            }
            size="sm"
            variant={available ? "default" : "outline"}
          >
            {available ? "В корзину" : "Аналог"}
          </Button>
        </div>
      </div>
    </article>
  )
}

function FilterButton({
  active,
  children,
  onClick,
}: {
  active: boolean
  children: React.ReactNode
  onClick: () => void
}) {
  return (
    <Button
      aria-pressed={active}
      className={
        active
          ? "border-zinc-950 bg-zinc-950 text-white hover:bg-zinc-800"
          : "bg-white"
      }
      onClick={onClick}
      size="sm"
      type="button"
      variant="outline"
    >
      {active ? <Check className="size-3.5" /> : null}
      {children}
    </Button>
  )
}

function FilterDialog({
  filters,
  facets,
  onChange,
}: {
  filters: Filters
  facets: CatalogFacets
  onChange: (filters: Filters) => void
}) {
  const [draft, setDraft] = useState(filters)
  const toggle = <T,>(values: T[], value: T) =>
    values.includes(value)
      ? values.filter((item) => item !== value)
      : [...values, value]

  return (
    <Dialog onOpenChange={(open) => open && setDraft(filters)}>
      <DialogTrigger asChild>
        <Button className="h-9 cursor-pointer" variant="outline">
          <SlidersHorizontal className="size-4" />
          Фильтры
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[calc(100dvh-24px)] overflow-y-auto sm:max-w-[560px]">
        <DialogHeader>
          <DialogTitle>Фильтры</DialogTitle>
          <DialogDescription className="sr-only">
            Выберите параметры каталога и покажите подходящие товары.
          </DialogDescription>
        </DialogHeader>
        <label className="flex h-14 cursor-pointer items-center justify-between rounded-xl border px-4 text-sm font-medium">
          Только в наличии
          <Checkbox
            checked={draft.available}
            onCheckedChange={(checked) =>
              setDraft((current) => ({ ...current, available: checked === true }))
            }
          />
        </label>
        <div className="space-y-3">
          <p className="text-sm font-semibold">Документы</p>
          <div aria-label="Документы" className="flex flex-wrap gap-2" role="group">
            {presenceOptions.map(({ value, label }) => (
              <FilterButton
                active={draft.hasDocuments === value}
                key={label}
                onClick={() =>
                  setDraft((current) => ({
                    ...current,
                    hasDocuments: value,
                    hasCertificates: value === false ? null : current.hasCertificates,
                  }))
                }
              >
                {label}
              </FilterButton>
            ))}
          </div>
        </div>
        <div className="space-y-3">
          <p className="text-sm font-semibold">Сертификат</p>
          <div aria-label="Сертификат" className="flex flex-wrap gap-2" role="group">
            {presenceOptions.map(({ value, label }) => (
              <FilterButton
                active={draft.hasCertificates === value}
                key={label}
                onClick={() =>
                  setDraft((current) => ({
                    ...current,
                    hasDocuments:
                      value === true && current.hasDocuments === false
                        ? null
                        : current.hasDocuments,
                    hasCertificates: value,
                  }))
                }
              >
                {label}
              </FilterButton>
            ))}
          </div>
        </div>
        <div className="space-y-3">
          <p className="text-sm font-semibold">Серия</p>
          <div className="flex flex-wrap gap-2">
            {facets.series.map((value) => (
              <FilterButton
                active={draft.series.includes(value)}
                key={value}
                onClick={() =>
                  setDraft((current) => ({
                    ...current,
                    series: toggle(current.series, value),
                  }))
                }
              >
                {value}
              </FilterButton>
            ))}
          </div>
        </div>
        <div className="space-y-3">
          <p className="text-sm font-semibold">Номинальный ток</p>
          <div className="flex flex-wrap gap-2">
            {numericOptions(facets.current).map((value) => (
              <FilterButton
                active={draft.currents.includes(value)}
                key={value}
                onClick={() =>
                  setDraft((current) => ({
                    ...current,
                    currents: toggle(current.currents, value),
                  }))
                }
              >
                {value}
              </FilterButton>
            ))}
          </div>
        </div>
        <div className="space-y-3">
          <p className="text-sm font-semibold">Отключающая способность</p>
          <div className="flex flex-wrap gap-2">
            {numericOptions(facets.breaking_capacity).map((value) => (
              <FilterButton
                active={draft.capacities.includes(value)}
                key={value}
                onClick={() =>
                  setDraft((current) => ({
                    ...current,
                    capacities: toggle(current.capacities, value),
                  }))
                }
              >
                {value}
              </FilterButton>
            ))}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2 pt-2">
          <Button
            onClick={() => {
              setDraft(emptyFilters)
              onChange(emptyFilters)
            }}
            variant="outline"
          >
            Сбросить
          </Button>
          <DialogClose asChild>
            <Button className="bg-zinc-950" onClick={() => onChange(draft)}>
              Показать товары
            </Button>
          </DialogClose>
        </div>
      </DialogContent>
    </Dialog>
  )
}

function CatalogPage({
  query,
  onSelect,
  onAdd,
}: {
  query: string
  onSelect: (product: Product) => void
  onAdd: (product: Product, quantity: number) => void
}) {
  const [products, setProducts] = useState<Product[]>([])
  const [filters, setFilters] = useState(emptyFilters)
  const [facets, setFacets] = useState<CatalogFacets>(demoFacets)
  const [sort, setSort] = useState("popular")
  const [category, setCategory] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [demoMode, setDemoMode] = useState(false)
  const [catalogError, setCatalogError] = useState(false)
  const catalogRef = useRef<HTMLDivElement>(null)
  const pageCount = Math.ceil(total / catalogPageSize)

  const catalogFilters = useMemo<CatalogFilters>(() => ({
    category,
    inStock: filters.available,
    series: filters.series,
    current: filters.currents,
    breakingCapacity: filters.capacities,
    hasDocuments: filters.hasDocuments,
    hasCertificates: filters.hasCertificates,
    sort: sort === "price-asc" ? "price_asc" : sort === "price-desc" ? "price_desc" : "relevance",
  }), [category, filters, sort])

  useEffect(() => {
    let active = true
    getProductFacets()
      .then((value) => active && setFacets(value))
      .catch(() => undefined)
    return () => {
      active = false
    }
  }, [])

  useEffect(() => {
    let active = true
    searchProducts(query, catalogFilters, (page - 1) * catalogPageSize)
      .then((response) => {
        if (!active) return
        setDemoMode(response.catalog_scope === "demo_subset")
        setCatalogError(false)
        setProducts(response.items)
        setTotal(response.total)
      })
      .catch(() => {
        if (!active) return
        setDemoMode(demoEnabled)
        setCatalogError(!demoEnabled)
        setProducts(demoEnabled ? demoCatalog.slice((page - 1) * catalogPageSize, page * catalogPageSize) : [])
        setTotal(demoEnabled ? demoCatalog.length : 0)
      })
      .finally(() => active && setLoading(false))
    return () => {
      active = false
    }
  }, [query, catalogFilters, page])

  function changePage(nextPage: number) {
    if (nextPage === page || nextPage < 1 || nextPage > pageCount) return
    setLoading(true)
    setPage(nextPage)
    catalogRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })
  }

  const visible = useMemo(() => {
    const normalized = query.toLocaleLowerCase("ru")
    const values = products.filter((product) => {
      const characteristic = (...codes: string[]) =>
        product.characteristics?.find((item) => codes.includes(item.code))?.value
      const series = product.series ?? characteristic("SERIES")
      const current = characteristic("NOMINALNYY_TOK", "CURRENT")
      const capacity = characteristic(
        "NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST",
        "BREAKING_CAPACITY"
      )
      const matchesQuery =
        !normalized ||
        `${product.name} ${product.article} ${product.supplier_article}`
          .toLocaleLowerCase("ru")
          .includes(normalized)
      const hasDocuments = Boolean(product.documents?.length)
      const hasCertificate =
        product.documents?.some((document) => document.type === "certificate") ??
        false
      return (
        matchesQuery &&
        (!category || product.category === category) &&
        (!filters.available || Number(product.available_quantity) > 0) &&
        (filters.hasDocuments === null ||
          filters.hasDocuments === hasDocuments) &&
        (filters.hasCertificates === null ||
          filters.hasCertificates === hasCertificate) &&
        (!filters.series.length || (series && filters.series.includes(series))) &&
        (!filters.currents.length || (current && filters.currents.includes(current))) &&
        (!filters.capacities.length ||
          (capacity && filters.capacities.includes(capacity)))
      )
    })
    return [...values].sort((a, b) =>
      sort === "price-asc"
        ? Number(a.price) - Number(b.price)
        : sort === "price-desc"
          ? Number(b.price) - Number(a.price)
          : 0
    )
  }, [category, filters, products, query, sort])

  return (
    <div className="px-4 py-6 md:px-10 md:py-7" ref={catalogRef}>
      <p className="text-xs text-muted-foreground">
        Каталог / Товары ЕКТ
      </p>
      <div className="mt-2 flex items-baseline gap-3">
        <h1 className="text-[26px] leading-8 font-bold">
          Каталог товаров
        </h1>
        <span className="text-xs text-muted-foreground">
          {loading ? "Ищем товары…" : productCount(total)}
        </span>
        {demoMode ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <Badge className="cursor-help" tabIndex={0} variant="secondary">
                Тестовая выборка ЕКТ
              </Badge>
            </TooltipTrigger>
            <TooltipContent side="bottom">
              Источник данных: https://ekt.kz/api
            </TooltipContent>
          </Tooltip>
        ) : null}
        {catalogError ? <p role="alert">Каталог временно недоступен. Повторите поиск позже.</p> : null}
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-3">
        <Select
          onValueChange={(value) => {
            setLoading(true)
            setCategory(value === "all" ? null : value)
            setPage(1)
          }}
          value={category ?? "all"}
        >
          <SelectTrigger aria-label="Категория" className="h-9 w-[220px] max-w-full cursor-pointer">
            <SelectValue placeholder="Все категории" />
          </SelectTrigger>
          <SelectContent align="start" position="popper">
            <SelectItem value="all">Все категории</SelectItem>
            {facets.categories.map((value) => (
              <SelectItem key={value} value={value}>{value}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <FilterDialog
          facets={facets}
          filters={filters}
          onChange={(next) => {
            setLoading(true)
            setFilters(next)
            setPage(1)
          }}
        />
        <div aria-label="Сортировка" className="flex w-fit max-w-full items-center gap-0.5 rounded-lg border bg-zinc-50 p-0.5 md:ml-auto" role="group">
          {[
            ["popular", "Популярные"],
            ["price-asc", "Дешевле"],
            ["price-desc", "Дороже"],
          ].map(([value, label]) => (
            <Button
              aria-pressed={sort === value}
              className={sort === value ? "h-8 cursor-pointer bg-white px-2.5 text-zinc-950 shadow-sm hover:bg-white" : "h-8 cursor-pointer px-2.5 text-muted-foreground hover:bg-white/70"}
              key={value}
              onClick={() => {
                setLoading(true)
                setSort(value)
                setPage(1)
              }}
              size="sm"
              variant="ghost"
            >
              {label}
            </Button>
          ))}
        </div>
      </div>
      {loading ? (
        <div className="mt-7 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {Array.from({ length: 10 }).map((_, index) => (
            <Skeleton className="h-[315px] rounded-xl" key={index} />
          ))}
        </div>
      ) : visible.length ? (
        <div className="mt-7 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {visible.map((product) => (
            <ProductTile
              key={product.id}
              onAdd={onAdd}
              onSelect={onSelect}
              product={product}
            />
          ))}
        </div>
      ) : (
        <div className="grid min-h-80 place-items-center text-center">
          <div>
            <Search className="mx-auto mb-3 size-8 text-zinc-300" />
            <p className="font-semibold">Товары не найдены</p>
            <Button
              className="mt-3"
              onClick={() => {
                window.dispatchEvent(
                  new CustomEvent("catalog-search", { detail: "" })
                )
                setFilters(emptyFilters)
                setLoading(true)
                setPage(1)
              }}
              variant="outline"
            >
              Сбросить фильтры
            </Button>
          </div>
        </div>
      )}
      {!loading && pageCount > 1 ? (
        <nav aria-label="Страницы каталога" className="mt-7 flex flex-wrap items-center justify-center gap-1.5">
          <Button aria-label="Предыдущая страница" className="size-9" disabled={page === 1} onClick={() => changePage(page - 1)} size="icon" variant="outline">
            <ChevronLeft className="size-4" />
          </Button>
          {paginationItems(page, pageCount).map((item, index) =>
            item === "…" ? (
              <span aria-hidden="true" className="px-1 text-muted-foreground" key={`gap-${index}`}>…</span>
            ) : (
              <Button
                aria-current={item === page ? "page" : undefined}
                aria-label={`Страница ${item}`}
                className="size-9 cursor-pointer"
                key={item}
                onClick={() => changePage(item)}
                size="icon"
                variant={item === page ? "default" : "outline"}
              >
                {item}
              </Button>
            )
          )}
          <Button aria-label="Следующая страница" className="size-9" disabled={page === pageCount} onClick={() => changePage(page + 1)} size="icon" variant="outline">
            <ChevronRight className="size-4" />
          </Button>
        </nav>
      ) : null}
    </div>
  )
}

function Breadcrumbs({
  category,
  onBack,
}: {
  category?: string | null
  onBack: () => void
}) {
  return (
    <div className="mb-4 flex items-center gap-2 text-xs text-muted-foreground">
      <Button className="h-auto p-0 text-xs text-muted-foreground hover:bg-transparent hover:underline" onClick={onBack} type="button" variant="ghost">
        Каталог
      </Button>
      <span>/</span>
      <Button className="h-auto p-0 text-xs text-muted-foreground hover:bg-transparent hover:underline" onClick={onBack} type="button" variant="ghost">
        {category ?? "Все товары"}
      </Button>
    </div>
  )
}

function ProductPage({
  initialProduct,
  onBack,
  onAdd,
}: {
  initialProduct: Product
  onBack: () => void
  onAdd: (product: Product, quantity: number) => void
}) {
  const [product, setProduct] = useState(initialProduct)
  const [quantity, setQuantity] = useState(1)
  const [freshUnavailable, setFreshUnavailable] = useState(false)
  useEffect(() => {
    let active = true
    getProduct(initialProduct.id)
      .then((value) => active && setProduct(value))
      .catch(() => active && setFreshUnavailable(true))
    return () => {
      active = false
    }
  }, [initialProduct])
  const available = Number(product.available_quantity) > 0
  const brand =
    product.brand ??
    product.characteristics?.find((item) => item.code === "TORGOVAYA_MARKA")
      ?.value

  return (
    <div className="px-4 py-6 md:px-10">
      <Breadcrumbs category={product.category} onBack={onBack} />
      <div className="grid gap-6 md:grid-cols-[minmax(300px,40%)_1fr] md:gap-10">
        <ProductImage detail product={product} />
        <div>
          {brand ? <Badge variant="outline">{brand}</Badge> : null}
          <h1 className="mt-3 text-2xl leading-tight font-bold md:text-[28px]">
            {product.name}
          </h1>
          <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground">
            <span>Арт. {product.article}</span>
            {product.supplier_article ? (
              <span>Код пр-ля {product.supplier_article}</span>
            ) : null}
          </div>
          {freshUnavailable ? (
            <p className="mt-2 text-xs text-muted-foreground" role="status">
              Показаны сохранённые данные ЕКТ. Обновление карточки сейчас недоступно.
            </p>
          ) : null}
          <Card className="mt-4 gap-0 py-0 shadow-none">
            <CardContent className="p-4">
              <div className="flex items-center justify-between">
                <strong className="text-[30px]">
                  {formatPrice(product.price)}
                </strong>
                <Badge
                  className="border-emerald-100 bg-emerald-50 text-emerald-600"
                  variant="outline"
                >
                  {available
                    ? `В наличии · ${product.available_quantity} шт.`
                    : "Нет в наличии"}
                </Badge>
              </div>
              <div className="mt-3 grid grid-cols-[140px_1fr] gap-2">
                <div className="grid h-11 grid-cols-3 rounded-lg border">
                  <Button
                    aria-label="Уменьшить количество"
                    className="h-full rounded-none p-0"
                    onClick={() =>
                      setQuantity((value) => Math.max(1, value - 1))
                    }
                    type="button"
                    variant="ghost"
                  >
                    <Minus className="mx-auto size-3" />
                  </Button>
                  <span className="grid place-items-center text-sm">
                    {quantity}
                  </span>
                  <Button
                    aria-label="Увеличить количество"
                    className="h-full rounded-none p-0"
                    onClick={() =>
                      setQuantity((value) =>
                        Math.max(
                          1,
                          Math.min(
                            Number(product.available_quantity),
                            value + 1
                          )
                        )
                      )
                    }
                    type="button"
                    variant="ghost"
                  >
                    <Plus className="mx-auto size-3" />
                  </Button>
                </div>
                <Button
                  className="h-11 bg-[#c2410c] hover:bg-[#9a3412]"
                  disabled={!available}
                  onClick={() => onAdd(product, quantity)}
                >
                  В корзину
                </Button>
              </div>
              <Button
                className="mt-3 h-11 w-full"
                onClick={() =>
                  askAssistant(
                    `Расскажи о товаре ${product.supplier_article ?? product.article}: характеристики, наличие и сертификаты`
                  )
                }
                variant="outline"
              >
                Спросить ассистента о товаре
              </Button>
            </CardContent>
          </Card>
          <Card className="mt-3 gap-0 py-0 shadow-none">
            <CardContent className="p-0">
              <div className="flex justify-between border-b px-4 py-3 text-sm font-semibold">
                <span>Наличие на складах</span>
                <span className="font-normal text-muted-foreground">
                  {product.stores?.length ?? 0} складов
                </span>
              </div>
              {product.stores?.slice(0, 4).map((store) => (
                <div
                  className="flex items-center justify-between border-b px-4 py-2.5 text-sm"
                  key={`${store.id}-${store.name}`}
                >
                  <span className="flex items-center gap-2">
                    <span className="size-1.5 rounded-full bg-emerald-500" />
                    {store.name}
                  </span>
                  <span>{store.quantity} шт.</span>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      </div>
      <Tabs className="mt-8" defaultValue="specs">
        <TabsList>
          <TabsTrigger value="specs">Характеристики</TabsTrigger>
          <TabsTrigger value="description">Описание</TabsTrigger>
          <TabsTrigger value="documents">Документы</TabsTrigger>
        </TabsList>
        <TabsContent value="specs">
          <div className="mt-3 max-w-3xl overflow-hidden rounded-xl border">
            {product.characteristics?.map((item) => (
              <div
                className="grid grid-cols-2 border-b px-4 py-3 text-sm last:border-0"
                key={item.code}
              >
                <span className="text-muted-foreground">{item.name}</span>
                <span>{item.value}</span>
              </div>
            ))}
          </div>
        </TabsContent>
        <TabsContent value="description">
          <p className="mt-3 max-w-3xl rounded-xl border p-4 text-sm leading-6 text-muted-foreground">
            {product.description ?? "Описание уточняется в каталоге."}
          </p>
        </TabsContent>
        <TabsContent value="documents">
          <div className="mt-3 max-w-3xl rounded-xl border p-4">
            {product.documents?.length ? (
              product.documents.map((document, index) => {
                const href = documentHref(document.url)
                const content = (
                  <>
                    <FileText className="size-4 shrink-0 text-[#c2410c]" />
                    <span className="min-w-0 flex-1">{document.title}</span>
                    {href ? <ExternalLink className="size-4 shrink-0" /> : null}
                  </>
                )
                return href ? (
                  <a
                    className="flex items-center gap-3 rounded-lg px-2 py-2 text-sm hover:bg-zinc-50 hover:text-[#c2410c] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#c2410c]"
                    href={href}
                    key={`${document.type}-${document.url}-${index}`}
                    rel="noopener noreferrer"
                    target="_blank"
                  >
                    {content}
                  </a>
                ) : (
                  <div
                    className="flex items-center gap-3 px-2 py-2 text-sm text-muted-foreground"
                    key={`${document.type}-${index}`}
                  >
                    {content}
                  </div>
                )
              })
            ) : (
              <p className="text-sm text-muted-foreground">
                В доступной карточке ЕКТ документов нет
              </p>
            )}
          </div>
        </TabsContent>
      </Tabs>
    </div>
  )
}

function CartPage({ onBack }: { onBack: () => void }) {
  const [cart, setCart] = useState<Cart | null>(null)
  const [loadError, setLoadError] = useState(false)
  const [pendingRemoval, setPendingRemoval] = useState<Cart["items"][number] | null>(null)
  const [removing, setRemoving] = useState(false)
  const [mutationError, setMutationError] = useState<string | null>(null)
  const load = useCallback(
    () =>
      getCart()
        .then((value) => { setCart(value); setLoadError(false) })
        .catch(() => {
          if (demoEnabled) setCart(readDemoCart())
          else setLoadError(true)
        }),
    []
  )
  useEffect(() => {
    load()
    window.addEventListener("cart-updated", load)
    window.addEventListener("demo-cart-updated", load)
    return () => {
      window.removeEventListener("cart-updated", load)
      window.removeEventListener("demo-cart-updated", load)
    }
  }, [load])
  const totalQuantity =
    cart?.items.reduce((total, item) => total + Number(item.quantity), 0) ?? 0

  async function confirmRemoval() {
    if (!cart || !pendingRemoval || removing) return
    setRemoving(true)
    setMutationError(null)
    try {
      const updated =
        demoEnabled && cart.id === "demo-cart"
          ? removeDemoCartItem(pendingRemoval.product_id)
          : await removeCartItem(pendingRemoval.product_id, cart.revision)
      setCart(updated)
      setPendingRemoval(null)
    } catch (error) {
      const apiError = error as ChatApiError
      setMutationError(apiError.message || "Не удалось удалить товар. Повторите попытку.")
      setPendingRemoval(null)
      if (apiError.code === "CART_CHANGED") await load()
    } finally {
      setRemoving(false)
    }
  }

  return (
    <div className="px-4 py-6 md:px-10">
      <h1 className="text-[26px] font-bold">Корзина</h1>
      {mutationError ? (
        <p className="mt-4 text-sm text-red-700" role="alert">
          {mutationError}
        </p>
      ) : null}
      {loadError ? (
        <div role="alert" className="mt-5">
          <p>Не удалось загрузить корзину. Проверьте соединение и повторите попытку.</p>
          <Button className="mt-3" onClick={load}>Повторить</Button>
        </div>
      ) : !cart ? (
        <Skeleton className="mt-5 h-40" />
      ) : !cart.items.length ? (
        <div className="mt-5 grid min-h-[180px] place-items-center rounded-xl border border-dashed text-center">
          <div>
            <p className="font-semibold">Корзина пуста</p>
            <p className="mt-2 text-sm text-muted-foreground">
              Добавьте товары из каталога или через ассистента — он спросит
              подтверждение.
            </p>
            <Button className="mt-3 bg-zinc-950" onClick={onBack}>
              Перейти в каталог
            </Button>
          </div>
        </div>
      ) : (
        <div className="mt-5 grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="min-h-[250px] overflow-hidden rounded-xl border">
            {cart.items.map((item) => {
              const matchingProduct = demoCatalog.find(
                (product) => product.id === item.product_id
              )
              return (
                <div
                  className="flex flex-wrap items-center gap-3 border-b p-4 last:border-0 md:flex-nowrap"
                  key={item.product_id}
                >
                  <div className="grid size-16 shrink-0 place-items-center rounded-lg border bg-white">
                    {matchingProduct?.image_url ? (
                      <img
                        className="size-full object-contain p-2"
                        src={matchingProduct.image_url}
                        alt=""
                      />
                    ) : (
                      <Package className="size-6 text-zinc-300" />
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold">{item.name}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {matchingProduct
                        ? `Арт. ${matchingProduct.article} · `
                        : ""}
                      {formatPrice(item.unit_price)} / шт.
                    </p>
                  </div>
                  <span className="rounded-lg border px-4 py-2 text-sm">
                    {item.quantity} шт.
                  </span>
                  <strong className="ml-auto text-sm whitespace-nowrap">
                    {formatPrice(item.line_total)}
                  </strong>
                  <Button
                    aria-label={`Удалить ${item.name} из корзины`}
                    className="cursor-pointer text-red-700 hover:bg-red-50 hover:text-red-800"
                    onClick={() => {
                      setMutationError(null)
                      setPendingRemoval(item)
                    }}
                    size="sm"
                    variant="ghost"
                  >
                    <Trash2 className="size-4" />
                    Удалить
                  </Button>
                </div>
              )
            })}
          </div>
          <div className="h-fit rounded-xl border p-4">
            <div className="flex justify-between text-sm">
              <span className="text-muted-foreground">Позиций</span>
              <span>{cart.line_count}</span>
            </div>
            <div className="mt-3 flex justify-between border-b pb-3 text-sm">
              <span className="text-muted-foreground">Товаров</span>
              <span>{totalQuantity} шт.</span>
            </div>
            <div className="mt-3 flex items-center justify-between gap-2">
              <span className="text-sm font-semibold">
                Итого · {totalQuantity} шт.
              </span>
              <strong className="text-xl whitespace-nowrap">
                {formatPrice(cart.total)}
              </strong>
            </div>
            <Button className="mt-4 w-full bg-[#c2410c]" disabled>
              Оформить заказ
            </Button>
            <Button className="mt-2 w-full" onClick={onBack} variant="outline">
              Продолжить покупки
            </Button>
          </div>
        </div>
      )}
      <Dialog
        open={pendingRemoval !== null}
        onOpenChange={(open) => {
          if (!open && !removing) setPendingRemoval(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Удалить товар?</DialogTitle>
            <DialogDescription>
              {pendingRemoval?.name} будет удалён из корзины.
            </DialogDescription>
          </DialogHeader>
          <div className="flex justify-end gap-2">
            <Button
              disabled={removing}
              onClick={() => setPendingRemoval(null)}
              variant="outline"
            >
              Отмена
            </Button>
            <Button
              className="bg-red-700 hover:bg-red-800"
              disabled={removing}
              onClick={confirmRemoval}
            >
              {removing ? "Удаляем…" : "Удалить"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}

export function Storefront() {
  const [page, setPage] = useState<Page>(
    window.location.pathname.startsWith("/cart")
      ? { name: "cart" }
      : { name: "catalog" }
  )
  const [authOpen, setAuthOpen] = useState(false)
  const [cartCount, setCartCount] = useState(0)
  const [catalogQuery, setCatalogQuery] = useState("")
  const [cartAction, setCartAction] = useState<CartAction | null>(null)
  const cartActionRequest = useRef(0)
  const refreshCart = useCallback(
    () =>
      getCart()
        .then((cart) => setCartCount(cart.line_count))
        .catch(() => { if (demoEnabled) setCartCount(readDemoCart().line_count) }),
    []
  )
  useEffect(() => {
    refreshCart()
    window.addEventListener("cart-updated", refreshCart)
    window.addEventListener("demo-cart-updated", refreshCart)
    return () => {
      window.removeEventListener("cart-updated", refreshCart)
      window.removeEventListener("demo-cart-updated", refreshCart)
    }
  }, [refreshCart])
  useEffect(() => {
    const handleSearch = (event: Event) => {
      setCatalogQuery((event as CustomEvent<string>).detail.trim())
      setPage({ name: "catalog" })
      window.history.replaceState({}, "", "/")
      window.scrollTo({ top: 0, behavior: "smooth" })
    }
    window.addEventListener("catalog-search", handleSearch)
    return () => window.removeEventListener("catalog-search", handleSearch)
  }, [])
  const go = (next: Page) => {
    setPage(next)
    window.history.replaceState({}, "", next.name === "cart" ? "/cart" : "/")
    window.scrollTo({ top: 0, behavior: "smooth" })
  }

  const closeCartAction = () => {
    cartActionRequest.current += 1
    setCartAction(null)
  }

  const openCartAction = async (product: Product, quantity: number) => {
    const request = ++cartActionRequest.current
    setCartAction({
      product,
      quantity,
      proposal: null,
      demo: false,
      status: "preparing",
      error: null,
    })

    try {
      const proposal = await prepareCartProposal(product.id, quantity)
      if (request === cartActionRequest.current) {
        setCartAction((current) =>
          current ? { ...current, proposal, status: "ready" } : null
        )
      }
    } catch (error) {
      if (request !== cartActionRequest.current) return
      const apiError = error as ChatApiError
      let displayError: unknown = error
      if (demoEnabled && !apiError.code && demoCatalog.some((item) => item.id === product.id)) {
        try {
          const proposal = prepareDemoProposal(product.id, quantity)
          setCartAction((current) =>
            current
              ? { ...current, proposal, demo: true, status: "ready" }
              : null
          )
          return
        } catch (demoError) {
          displayError = demoError
        }
      }
      setCartAction((current) =>
        current
          ? {
              ...current,
              status: "error",
              error:
                displayError instanceof Error
                  ? displayError.message
                  : "Не удалось проверить товар",
            }
          : null
      )
    }
  }

  const confirmCartAction = async () => {
    if (!cartAction?.proposal || cartAction.status !== "ready") return
    const request = cartActionRequest.current
    const { proposal, demo } = cartAction
    setCartAction((current) =>
      current ? { ...current, status: "adding", error: null } : null
    )
    try {
      const result = demo
        ? resolveDemoProposal(proposal, "confirm")
        : await resolveProposal(proposal.id, "confirm")
      if (request !== cartActionRequest.current) return
      if (!result.cart) {
        throw new Error("Товар не добавлен. Проверьте остаток и попробуйте снова.")
      }
      if (!demo) window.dispatchEvent(new Event("cart-updated"))
      setCartAction((current) =>
        current ? { ...current, status: "done" } : null
      )
    } catch (error) {
      if (request !== cartActionRequest.current) return
      setCartAction((current) =>
        current
          ? {
              ...current,
              status: "error",
              error:
                error instanceof Error
                  ? error.message
                  : "Не удалось добавить товар",
            }
          : null
      )
    }
  }

  return (
    <div className="min-h-[inherit] bg-white">
      <Header
        cartCount={cartCount}
        onAuth={() => setAuthOpen(true)}
        onCart={() => go({ name: "cart" })}
        onHome={() => go({ name: "catalog" })}
      />
      {page.name === "catalog" ? (
        <CatalogPage
          key={catalogQuery}
          onAdd={openCartAction}
          query={catalogQuery}
          onSelect={(product) => go({ name: "product", product })}
        />
      ) : null}
      {page.name === "product" ? (
        <ProductPage
          initialProduct={page.product}
          onAdd={openCartAction}
          onBack={() => go({ name: "catalog" })}
        />
      ) : null}
      {page.name === "cart" ? (
        <CartPage onBack={() => go({ name: "catalog" })} />
      ) : null}
      <AuthDialog onOpenChange={setAuthOpen} open={authOpen} />
      <Dialog
        onOpenChange={(open) => {
          if (!open) closeCartAction()
        }}
        open={cartAction !== null}
      >
        <DialogContent className="sm:max-w-[420px]">
          <DialogHeader>
            <DialogTitle>
              {cartAction?.status === "done" ? "Товар добавлен" : "Добавить в корзину?"}
            </DialogTitle>
            <DialogDescription>
              {cartAction?.status === "done"
                ? "Товар уже в корзине."
                : "Корзина изменится только после вашего подтверждения."}
            </DialogDescription>
          </DialogHeader>
          {cartAction ? (
            <div className="space-y-4">
              <div className="flex items-center gap-3 rounded-lg border p-3">
                {cartAction.product.image_url ? (
                  <img
                    alt=""
                    className="size-16 shrink-0 object-contain"
                    src={cartAction.product.image_url}
                  />
                ) : (
                  <Package className="size-12 shrink-0 text-zinc-300" />
                )}
                <div className="min-w-0">
                  <p className="text-sm font-medium">{cartAction.product.name}</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {cartAction.quantity} шт. · Арт. {cartAction.product.article}
                  </p>
                </div>
              </div>
              {cartAction.demo ? <Badge variant="secondary">Демо</Badge> : null}
              {cartAction.status === "preparing" ? (
                <Skeleton className="h-10 w-full" />
              ) : cartAction.proposal && cartAction.status !== "error" ? (
                <div className="flex justify-between text-sm">
                  <span>Итого</span>
                  <strong>{formatPrice(cartAction.proposal.total)}</strong>
                </div>
              ) : null}
              {cartAction.error ? (
                <p className="text-sm text-destructive" role="alert">
                  {cartAction.error}
                </p>
              ) : null}
              {cartAction.status === "done" ? (
                <Button
                  className="h-11 w-full bg-[#c2410c] hover:bg-[#9a3412]"
                  onClick={() => {
                    closeCartAction()
                    go({ name: "cart" })
                  }}
                >
                  Перейти в корзину
                </Button>
              ) : (
                <div className="grid grid-cols-2 gap-2">
                  <Button onClick={closeCartAction} variant="outline">
                    Отмена
                  </Button>
                  <Button
                    className="bg-[#c2410c] hover:bg-[#9a3412]"
                    disabled={cartAction.status !== "ready"}
                    onClick={confirmCartAction}
                  >
                    {cartAction.status === "adding" ? "Добавляем…" : "Добавить"}
                  </Button>
                </div>
              )}
            </div>
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  )
}
