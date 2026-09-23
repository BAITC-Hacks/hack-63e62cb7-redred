import { lazy, Suspense } from "react"
import { ChatWidget } from "@/components/chat/chat-widget"
import { Storefront } from "@/components/store/storefront"

const AdminPage = lazy(() => import("@/components/admin/admin-page"))

export function App() {
  const pathname = window.location.pathname.slice(import.meta.env.BASE_URL.length - 1)
  if (pathname === "/admin" || pathname.startsWith("/admin/")) {
    return <Suspense fallback={<div role="status" className="p-8 text-sm text-muted-foreground">Загрузка панели…</div>}><AdminPage /></Suspense>
  }
  return (
    <main className="min-h-svh bg-zinc-100 p-0 md:p-6">
      <div className="mx-auto min-h-svh max-w-[1440px] overflow-hidden border bg-white shadow-sm md:min-h-[calc(100svh-48px)] md:rounded-xl">
        <Storefront />
      </div>
      <ChatWidget />
    </main>
  )
}

export default App
