export type ProductStore = {
  id: number | string | null
  name: string
  quantity: string
}

export type ProductCharacteristic = {
  code: string
  name: string
  value: string
}

export type ProductDocument = {
  type: string
  title: string
  url: string
  source_url?: string
}

export type Product = {
  id: number
  article: string
  supplier_article?: string | null
  name: string
  category?: string | null
  series?: string | null
  brand?: string | null
  price: string | null
  currency: string
  available_quantity: string
  unit?: string | null
  image_url?: string | null
  product_url?: string | null
  description?: string | null
  quantity_step?: string | null
  checked_at?: string
  stock_scope?: string
  stores?: ProductStore[]
  characteristics?: ProductCharacteristic[]
  documents?: ProductDocument[]
  data_warnings?: string[]
}

export type CartProposalItem = {
  product_id: number
  name: string
  quantity: string
  unit_price: string
  line_total: string
  unit?: string
}

export type CartProposal = {
  id: string
  status: "pending" | "confirmed" | "cancelled" | "expired" | "replaced"
  expires_at: string
  items: CartProposalItem[]
  total: string
  currency: string
}

export type CartItem = {
  product_id: number
  name: string
  quantity: string
  unit: string
  unit_price: string
  line_total: string
  price_checked_at: string
}

export type Cart = {
  id: string
  revision: number
  items: CartItem[]
  line_count: number
  total: string
  currency: string
  cart_url: string
}

export type ChatResponse = {
  message_id: string
  language: "ru" | "kk" | "en"
  text: string
  products: Product[]
  proposal: CartProposal | null
  cart: Cart | null
  cart_url: string | null
  warnings: string[]
}

export type ProposalResolution = {
  status?: "cancelled"
  proposal?: CartProposal
  cart?: Cart
  cart_url?: string
}

export type CatalogResponse = {
  items: Product[]
  total: number
  catalog_scope: string
}

export type CatalogFacets = {
  categories: string[]
  series: string[]
  current: string[]
  breaking_capacity: string[]
}

export type AuthUser = {
  id: string
  email: string
  name: string | null
  role: string
}

export type AuthState = {
  authenticated: boolean
  user: AuthUser | null
  csrf_token: string | null
  expires_at: string | null
}

export type AuthResult = {
  user: AuthUser
  csrf_token: string
  expires_at: string
  cart_merge_draft: {
    id: string
    status: "pending" | "confirmed"
    items: Array<{
      product_id: number
      name: string
      quantity: string
      unit: string
      unit_price: string
      price_checked_at: string
    }>
    proposal_id: string | null
    created_at: string
  } | null
}

export type StoredChatMessage = {
  id: string
  role: "user" | "assistant"
  text: string
  status: string
  result: ChatResponse | null
}

export type ChatHistoryResponse = {
  items: StoredChatMessage[]
  next_before_id: string | null
}

type SessionResponse = {
  csrf_token: string
  expires_at: string
}

type AttachmentResponse = {
  attachment_id: string
  filename: string
  media_type: string
  size: number
}

type ApiErrorBody = {
  error?: {
    code?: string
    message?: string
  }
}

export class ChatApiError extends Error {
  code?: string

  constructor(message: string, code?: string) {
    super(message)
    this.name = "ChatApiError"
    this.code = code
  }
}

let sessionRequest: Promise<SessionResponse> | null = null

async function readError(response: Response) {
  let body: ApiErrorBody | null = null
  try {
    body = (await response.json()) as ApiErrorBody
  } catch {
    // The backend may be unavailable or return a proxy error without JSON.
  }

  throw new ChatApiError(
    body?.error?.message ?? `Сервис вернул ошибку ${response.status}`,
    body?.error?.code
  )
}

async function createSession() {
  const response = await fetch("/api/session", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  })

  if (!response.ok) await readError(response)
  return (await response.json()) as SessionResponse
}

async function getSession() {
  sessionRequest ??= createSession().catch((error: unknown) => {
    sessionRequest = null
    throw error
  })
  return sessionRequest
}

export async function getAuthState() {
  const response = await fetch("/api/auth/me", { credentials: "include" })
  if (!response.ok) await readError(response)
  return (await response.json()) as AuthState
}

async function authMutation(
  action: "login" | "register",
  payload: { email: string; password: string; name?: string }
) {
  const session = await getSession()
  const response = await fetch(`/api/auth/${action}`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": session.csrf_token,
    },
    body: JSON.stringify(payload),
  })
  if (!response.ok) await readError(response)
  const result = (await response.json()) as AuthResult
  sessionRequest = Promise.resolve({
    csrf_token: result.csrf_token,
    expires_at: result.expires_at,
  })
  window.dispatchEvent(new Event("auth-changed"))
  window.dispatchEvent(new Event("cart-updated"))
  return result
}

export function loginClient(email: string, password: string) {
  return authMutation("login", { email, password })
}

export function registerClient(name: string, email: string, password: string) {
  return authMutation("register", { name, email, password })
}

export async function logoutClient() {
  const session = await getSession()
  const response = await fetch("/api/auth/logout", {
    method: "POST",
    credentials: "include",
    headers: { "X-CSRF-Token": session.csrf_token },
  })
  if (!response.ok) await readError(response)
  sessionRequest = null
  window.dispatchEvent(new Event("auth-changed"))
  window.dispatchEvent(new Event("cart-updated"))
}

export async function getChatHistory() {
  await getSession()
  const response = await fetch("/api/chat/messages?limit=50", {
    credentials: "include",
  })
  if (!response.ok) await readError(response)
  return (await response.json()) as ChatHistoryResponse
}

async function uploadAttachment(file: File, csrfToken: string) {
  const formData = new FormData()
  formData.append("file", file)

  const response = await fetch("/api/attachments", {
    method: "POST",
    credentials: "include",
    headers: { "X-CSRF-Token": csrfToken },
    body: formData,
  })

  if (!response.ok) await readError(response)
  return (await response.json()) as AttachmentResponse
}

export async function sendChatMessage(text: string, attachment?: File | null) {
  const session = await getSession()
  const attachmentIds: string[] = []

  if (attachment) {
    const uploaded = await uploadAttachment(attachment, session.csrf_token)
    attachmentIds.push(uploaded.attachment_id)
  }

  const response = await fetch("/api/chat/messages", {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": session.csrf_token,
    },
    body: JSON.stringify({
      request_id: crypto.randomUUID(),
      text,
      attachment_ids: attachmentIds,
      language: null,
    }),
  })

  if (!response.ok) await readError(response)
  return (await response.json()) as ChatResponse
}

export async function resolveProposal(
  proposalId: string,
  action: "confirm" | "cancel"
) {
  const session = await getSession()
  const response = await fetch(`/api/cart/proposals/${proposalId}/${action}`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": session.csrf_token,
    },
    body: action === "confirm" ? JSON.stringify({ confirmed: true }) : "{}",
  })

  if (!response.ok) await readError(response)
  return (await response.json()) as ProposalResolution
}

export async function prepareCartProposal(productId: number, quantity: number) {
  const session = await getSession()
  const response = await fetch("/api/cart/proposals", {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": session.csrf_token,
    },
    body: JSON.stringify({
      items: [{ product_id: productId, quantity: String(quantity) }],
    }),
  })

  if (!response.ok) await readError(response)
  return (await response.json()) as CartProposal
}

export async function getCart() {
  await getSession()
  const response = await fetch("/api/cart", {
    credentials: "include",
  })

  if (!response.ok) await readError(response)
  return (await response.json()) as Cart
}

export async function removeCartItem(productId: number, expectedRevision: number) {
  const session = await getSession()
  const response = await fetch(`/api/cart/items/${productId}`, {
    method: "DELETE",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": session.csrf_token,
    },
    body: JSON.stringify({ confirmed: true, expected_revision: expectedRevision }),
  })
  if (!response.ok) await readError(response)
  const cart = (await response.json()) as Cart
  window.dispatchEvent(new Event("cart-updated"))
  return cart
}

export type CatalogFilters = {
  category?: string | null
  inStock?: boolean
  series?: string[]
  current?: string[]
  breakingCapacity?: string[]
  hasDocuments?: boolean | null
  hasCertificates?: boolean | null
  sort?: "relevance" | "price_asc" | "price_desc"
}

export async function searchProducts(query = "", filters: CatalogFilters = {}, offset = 0) {
  const params = new URLSearchParams({ q: query, limit: "20", offset: String(offset) })
  if (filters.category) params.set("category", filters.category)
  if (filters.inStock) params.set("in_stock", "true")
  for (const value of filters.series ?? []) params.append("series", value)
  for (const value of filters.current ?? []) params.append("current", value)
  for (const value of filters.breakingCapacity ?? []) {
    params.append("breaking_capacity", value)
  }
  if (filters.hasDocuments != null) {
    params.set("has_documents", String(filters.hasDocuments))
  }
  if (filters.hasCertificates != null) {
    params.set("has_certificates", String(filters.hasCertificates))
  }
  if (filters.sort && filters.sort !== "relevance") {
    params.set("sort", filters.sort)
  }
  const response = await fetch(`/api/products?${params}`)
  if (!response.ok) await readError(response)
  return (await response.json()) as CatalogResponse
}

export async function getProductFacets() {
  const response = await fetch("/api/products/facets")
  if (!response.ok) await readError(response)
  return (await response.json()) as CatalogFacets
}

export async function getProduct(productId: number) {
  const response = await fetch(`/api/products/${productId}`)
  if (!response.ok) await readError(response)
  return (await response.json()) as Product
}

export async function getPurchaseConditions() {
  const response = await fetch("/api/purchase-conditions")
  if (!response.ok) await readError(response)
  return (await response.json()) as Record<string, unknown>
}
