import { ChatWidget } from "@/components/chat/chat-widget"
import { Storefront } from "@/components/store/storefront"

export function App() {
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
