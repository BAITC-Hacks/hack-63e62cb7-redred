import type { Cart, CartProposal, ChatResponse, Product } from "@/lib/chat-api"

export const demoEnabled = import.meta.env.VITE_DEMO_MODE === "true"

export type DemoProduct = Product & {
  series: string
  current: number
  breakingCapacity: number
  poles: number
  description: string
}

const productBase = {
  category: "Автоматические выключатели",
  currency: "KZT",
  unit: "piece",
  product_url: null,
  stores: [
    { id: 1, name: "Астана", quantity: "8" },
    { id: 2, name: "Алматы", quantity: "5" },
    { id: 3, name: "Атырау", quantity: "3" },
  ],
  description:
    "Силовой автоматический выключатель для защиты электрических цепей от перегрузки и короткого замыкания.",
}

const productImages: Record<string, string> = {
  "027228":
    "https://ekt.kz/upload/iblock/1ca/8mdfx6517jvalt5da1n9865q2fzpj6jp/027228_av_drx250_mt_3f_160a_18ka_legrand_1.jpg",
  "027004":
    "https://ekt.kz/upload/iblock/592/ecrd69l3r236hyuttkj4lmnz1euly1a7/027004_av_drx125_mt_3f_40a_10ka_legrand_1.jpg",
  "027005":
    "https://ekt.kz/upload/iblock/e99/yqvedowbaop1fju39ky9pau7rmv6qj63/027005_av_drx125_mt_3f_50a_10ka_legrand_1.jpg",
  "027008":
    "https://ekt.kz/upload/iblock/dd5/m7bhmskgees6tmeh613tz5k3q10oldar/027008_av_drx125_mt_3f_100a_10ka_legrand_1.jpg",
  "027022":
    "https://ekt.kz/upload/iblock/076/0k32d23w57a0leyiunq6o8yltqfoynmw/027022_av_drx125_mt_3f_25a_20ka_legrand_1.jpg",
  "027024":
    "https://ekt.kz/upload/iblock/4ec/vurn6p0w0a2y95fktv0n66mrgjig0s08/027024_av_drx125_mt_3f_40a_20ka_legrand_1.jpg",
  "027028":
    "https://ekt.kz/upload/iblock/569/ys2rqxp1g7tpg7hxtuht2747ni73l53a/027028_av_drx125_mt_3f_10a_20ka_legrand_1.jpg",
  "027220":
    "https://ekt.kz/upload/iblock/ef5/utqen57lj3c7fjwfifxooatqtxcz3e2g/027220_av_drx125_mt_3f_63a_20ka_legrand_1.jpg",
  "027100":
    "https://ekt.kz/upload/iblock/053/vgn63u873t0vhpsub4zuzd6met8zgb91/027100_av_drx250_mt_3f_125a_18ka_legrand_1.jpg",
  "027103":
    "https://ekt.kz/upload/iblock/be3/e0ed5wa8ni54hge5z0fmtauhuzudpgx6/027103_av_drx250_mt_3f_200a_18ka_legrand_1.jpg",
  "027105":
    "https://ekt.kz/upload/iblock/75b/0w9qn9kumrcv9hqb7s6iegdf2g4b6cpw/027105_av_drx250_mt_3f_250a_18ka_legrand_1.jpg",
  "027112":
    "https://ekt.kz/upload/iblock/828/jx7tloq0z13y4ipsiqs83wuqry8t7xj3/027112_av_drx250_mt_3f_125a_25ka_legrand_1.jpg",
  "027115":
    "https://ekt.kz/upload/iblock/42d/gn2fvf89vn2tgkwzuiurcw99s9mgjbda/027115_av_drx250_mt_3f_200a_25ka_legrand_1.jpg",
  "027117":
    "https://ekt.kz/upload/iblock/9cd/utqxpur1mensee2kealvq68ta9kenbep/027117_av_drx250_mt_3f_250a_25ka_legrand_1.jpg",
}

function createProduct(
  id: number,
  article: string,
  supplierArticle: string,
  name: string,
  price: string,
  availableQuantity: string,
  series: string,
  current: number,
  breakingCapacity: number
): DemoProduct {
  return {
    ...productBase,
    id,
    article,
    supplier_article: supplierArticle,
    name,
    price,
    image_url: productImages[supplierArticle],
    available_quantity: availableQuantity,
    series,
    current,
    breakingCapacity,
    poles: 3,
    characteristics: [
      { code: "SERIES", name: "Серия", value: series },
      { code: "POLES", name: "Количество полюсов", value: "3" },
      { code: "CURRENT", name: "Номинальный ток", value: `${current} А` },
      {
        code: "BREAKING_CAPACITY",
        name: "Отключающая способность",
        value: `${breakingCapacity} кА`,
      },
    ],
    documents:
      id === 515291
        ? [
            {
              type: "certificate",
              title: "Сертификат соответствия — ссылка появится из API",
              url: "",
            },
          ]
        : [],
  }
}

export const demoCatalog: DemoProduct[] = [
  createProduct(
    515291,
    "200300285_",
    "027228",
    "027228 АВ DRX250 MT 3ф 160А 18ka Legrand (1)",
    "64920.00",
    "23",
    "DRX250 MT",
    160,
    18
  ),
  createProduct(
    515292,
    "200300273_",
    "027004",
    "027004 АВ DRX125 MT 3ф 40А 10ka Legrand (1)",
    "26930.00",
    "10",
    "DRX125 MT",
    40,
    10
  ),
  createProduct(
    515293,
    "200300274_",
    "027005",
    "027005 АВ DRX125 MT 3ф 50А 10ka Legrand (1)",
    "26930.00",
    "4",
    "DRX125 MT",
    50,
    10
  ),
  createProduct(
    515294,
    "200300272_",
    "027008",
    "027008 АВ DRX125 MT 3ф 100А 10ka Legrand (1)",
    "29190.00",
    "9",
    "DRX125 MT",
    100,
    10
  ),
  createProduct(
    515295,
    "200300276_",
    "027022",
    "027022 АВ DRX125 MT 3ф 25А 20ka Legrand (1)",
    "29810.00",
    "7",
    "DRX125 MT",
    25,
    20
  ),
  createProduct(
    515296,
    "200300277_",
    "027024",
    "027024 АВ DRX125 MT 3ф 40А 20ka Legrand (1)",
    "29810.00",
    "6",
    "DRX125 MT",
    40,
    20
  ),
  createProduct(
    515297,
    "200300275_",
    "027028",
    "027028 АВ DRX125 MT 3ф 100А 20ka Legrand (1)",
    "33130.00",
    "10",
    "DRX125 MT",
    100,
    20
  ),
  createProduct(
    515298,
    "200300279_",
    "027220",
    "027220 АВ DRX125 MT 3ф 63А 20ka Legrand (1)",
    "31620.00",
    "10",
    "DRX125 MT",
    63,
    20
  ),
  createProduct(
    515299,
    "200300284_",
    "027100",
    "027100 АВ DRX250 MT 3ф 125А 18ka Legrand (1)",
    "64780.00",
    "10",
    "DRX250 MT",
    125,
    18
  ),
  createProduct(
    515301,
    "200300286_",
    "027103",
    "027103 АВ DRX250 MT 3ф 200А 18ka Legrand (1)",
    "69880.00",
    "6",
    "DRX250 MT",
    200,
    18
  ),
  createProduct(
    515300,
    "200300280_",
    "027105",
    "027105 АВ DRX250 MT 3ф 250А 18ka Legrand (1)",
    "69910.00",
    "0",
    "DRX250 MT",
    250,
    18
  ),
  createProduct(
    515302,
    "200300281_",
    "027112",
    "027112 АВ DRX250 MT 3ф 125А 25ka Legrand (1)",
    "65450.00",
    "9",
    "DRX250 MT",
    125,
    25
  ),
  createProduct(
    515303,
    "200300283_",
    "027115",
    "027115 АВ DRX250 MT 3ф 200А 25ka Legrand (1)",
    "71160.00",
    "4",
    "DRX250 MT",
    200,
    25
  ),
  createProduct(
    515304,
    "200300287_",
    "027117",
    "027117 АВ DRX250 MT 3ф 250А 25ka Legrand (1)",
    "76940.00",
    "6",
    "DRX250 MT",
    250,
    25
  ),
]

const demoWarning = "Демо: данные каталога не подтверждены."

function createProposal(product: DemoProduct, quantity: string): CartProposal {
  const lineTotal = (Number(product.price) * Number(quantity)).toFixed(2)
  return {
    id: `demo-${crypto.randomUUID()}`,
    status: "pending",
    expires_at: new Date(Date.now() + 10 * 60 * 1000).toISOString(),
    items: [
      {
        product_id: product.id,
        name: product.name,
        quantity,
        unit_price: product.price ?? "0.00",
        line_total: lineTotal,
      },
    ],
    total: lineTotal,
    currency: product.currency,
  }
}

export function prepareDemoProposal(productId: number, quantity: number) {
  const product = demoCatalog.find((item) => item.id === productId)
  const existingQuantity = Number(
    readDemoCart().items.find((item) => item.product_id === productId)
      ?.quantity ?? "0"
  )
  if (
    !product ||
    !Number.isInteger(quantity) ||
    quantity < 1 ||
    existingQuantity + quantity > Number(product.available_quantity)
  ) {
    throw new Error("Недостаточно товара в наличии")
  }
  return createProposal(product, String(quantity))
}

function response(
  text: string,
  products: Product[] = [],
  proposal: CartProposal | null = null
): ChatResponse {
  return {
    message_id: crypto.randomUUID(),
    language: "ru",
    text,
    products,
    proposal,
    cart: null,
    cart_url: null,
    warnings: [demoWarning],
  }
}

export async function createDemoResponse(query: string, file?: File | null) {
  await new Promise((resolve) => window.setTimeout(resolve, 550))
  const normalized = query.toLocaleLowerCase("ru")

  if (file) {
    return response(
      `Файл «${file.name}» принят в демонстрационном режиме. Распознаны две позиции; перед добавлением проверьте соответствие артикулов.`,
      [demoCatalog[0], demoCatalog[2]]
    )
  }

  if (/аналог|замен/.test(normalized)) {
    return response(
      "Позиции 027105 сейчас нет в наличии. Нашёл два похожих товара той же серии. Первый совпадает по числу полюсов и отключающей способности, но отличается номинальным током. Проверьте совместимость перед заменой.",
      [demoCatalog[0], demoCatalog[8]]
    )
  }

  if (/достав|оплат|парт|услов/.test(normalized)) {
    return response(
      "Оплата: доступные способы подтверждаются при оформлении заказа.\n\nДоставка и самовывоз: условия зависят от города и склада. В источнике ekt.kz указаны разные пороги бесплатной доставки, поэтому точную сумму необходимо подтвердить у менеджера.\n\nМинимальная партия: общего правила в проверенных данных нет; учитывается шаг продажи конкретного товара."
    )
  }

  const matchedProduct = demoCatalog.find((product) =>
    [product.article, product.supplier_article]
      .filter(Boolean)
      .some((article) => normalized.includes(article!.toLocaleLowerCase("ru")))
  )

  if (matchedProduct || /налич|добав|корзин/.test(normalized)) {
    const product = matchedProduct ?? demoCatalog[0]
    const available = Number(product.available_quantity)
    const requested = Number(
      normalized.match(/(\d+)\s*(?:шт|штук|штуку|единиц)/)?.[1] ?? "1"
    )
    const wantsCart = /добав|корзин|подготов|нужно/.test(normalized)
    if (available === 0) {
      return response(
        `${product.supplier_article} сейчас нет в наличии. Могу показать аналоги.`,
        [product]
      )
    }
    if (wantsCart && requested > available) {
      return response(
        `В наличии только ${available} шт. Уточните количество для добавления.`,
        [product]
      )
    }
    return response(
      wantsCart
        ? `Нашёл ${product.supplier_article}. В наличии ${available} шт. Подготовил предложение на ${requested} шт.; корзина ждёт подтверждения.`
        : `Нашёл ${product.supplier_article}. В наличии ${available} шт.`,
      [product],
      wantsCart ? createProposal(product, String(requested)) : null
    )
  }

  return response(
    "В демо-выборке найден наиболее близкий товар. Для точного поиска укажите артикул, номинальный ток или серию.",
    [demoCatalog[0]]
  )
}

const demoCartKey = "ekt-demo-cart"

export function readDemoCart(): Cart {
  const empty: Cart = {
    id: "demo-cart",
    revision: 0,
    items: [],
    line_count: 0,
    total: "0.00",
    currency: "KZT",
    cart_url: "/cart",
  }

  try {
    const raw = localStorage.getItem(demoCartKey)
    return raw ? (JSON.parse(raw) as Cart) : empty
  } catch {
    return empty
  }
}

export function removeDemoCartItem(productId: number): Cart {
  const current = readDemoCart()
  const items = current.items.filter((item) => item.product_id !== productId)
  if (items.length === current.items.length) return current

  const cart: Cart = {
    ...current,
    revision: current.revision + 1,
    items,
    line_count: items.length,
    total: items
      .reduce((total, item) => total + Number(item.line_total), 0)
      .toFixed(2),
  }
  localStorage.setItem(demoCartKey, JSON.stringify(cart))
  window.dispatchEvent(new Event("demo-cart-updated"))
  return cart
}

export function resolveDemoProposal(
  proposal: CartProposal,
  action: "confirm" | "cancel"
): ChatResponse {
  if (action === "cancel") {
    return {
      ...response("Добавление отменено. Корзина не изменена."),
      proposal: { ...proposal, status: "cancelled" },
    }
  }

  const current = readDemoCart()
  const now = new Date().toISOString()
  const items = current.items.map((item) => ({ ...item }))

  for (const proposed of proposal.items) {
    const product = demoCatalog.find((item) => item.id === proposed.product_id)
    const alreadyInCart = Number(
      items.find((item) => item.product_id === proposed.product_id)?.quantity ??
        "0"
    )
    if (
      !product ||
      alreadyInCart + Number(proposed.quantity) >
        Number(product.available_quantity)
    ) {
      return {
        ...response("Недостаточно товара в наличии. Корзина не изменена."),
        proposal: { ...proposal, status: "cancelled" },
      }
    }
  }

  for (const proposed of proposal.items) {
    const existing = items.find(
      (item) => item.product_id === proposed.product_id
    )
    if (existing) {
      const quantity = Number(existing.quantity) + Number(proposed.quantity)
      existing.quantity = String(quantity)
      existing.line_total = (quantity * Number(existing.unit_price)).toFixed(2)
      existing.price_checked_at = now
    } else {
      items.push({
        product_id: proposed.product_id,
        name: proposed.name,
        quantity: proposed.quantity,
        unit: "piece",
        unit_price: proposed.unit_price,
        line_total: proposed.line_total,
        price_checked_at: now,
      })
    }
  }

  const cart: Cart = {
    id: "demo-cart",
    revision: current.revision + 1,
    items,
    line_count: items.length,
    total: items
      .reduce((total, item) => total + Number(item.line_total), 0)
      .toFixed(2),
    currency: "KZT",
    cart_url: "/cart",
  }
  localStorage.setItem(demoCartKey, JSON.stringify(cart))
  window.dispatchEvent(new Event("demo-cart-updated"))

  return {
    ...response(
      "Добавлено в демонстрационную корзину по вашему подтверждению."
    ),
    proposal: { ...proposal, status: "confirmed" },
    cart,
    cart_url: "/cart",
  }
}
