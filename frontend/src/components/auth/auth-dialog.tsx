import { useEffect, useState, type FormEvent } from "react"
import { Eye, EyeOff, UserRound } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  getAuthState,
  loginClient,
  logoutClient,
  registerClient,
  type AuthUser,
} from "@/lib/chat-api"

type AuthDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
}

function PasswordInput({
  id,
  autoComplete,
}: {
  id: string
  autoComplete: string
}) {
  const [visible, setVisible] = useState(false)

  return (
    <div className="relative">
      <Input
        autoComplete={autoComplete}
        className="h-11 pr-11"
        id={id}
        minLength={8}
        name="password"
        placeholder="Пароль"
        required
        type={visible ? "text" : "password"}
      />
      <Button
        aria-label={visible ? "Скрыть пароль" : "Показать пароль"}
        className="absolute top-1 right-1 size-9"
        onClick={() => setVisible((current) => !current)}
        size="icon"
        type="button"
        variant="ghost"
      >
        {visible ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
      </Button>
    </div>
  )
}

function AuthForm({
  mode,
  onSuccess,
}: {
  mode: "login" | "register"
  onSuccess: (user: AuthUser) => void
}) {
  const [notice, setNotice] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const email = String(data.get("email") ?? "").trim()
    const password = String(data.get("password") ?? "")
    const name = String(data.get("name") ?? "").trim()
    setPending(true)
    setError(null)
    try {
      const result =
        mode === "login"
          ? await loginClient(email, password)
          : await registerClient(name, email, password)
      onSuccess(result.user)
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Не удалось войти. Попробуйте ещё раз."
      )
    } finally {
      setPending(false)
    }
  }

  return (
    <form className="mt-5 space-y-3" onSubmit={submit}>
      {mode === "register" ? (
        <Input
          autoComplete="name"
          className="h-11"
          name="name"
          placeholder="Имя"
          required
        />
      ) : null}
      <Input
        aria-label="Электронная почта"
        autoComplete="email"
        className="h-11"
        name="email"
        placeholder="Электронная почта"
        required
        type="email"
      />
      <PasswordInput
        autoComplete={mode === "login" ? "current-password" : "new-password"}
        id={`auth-password-${mode}`}
      />
      {mode === "login" ? (
        <Button
          className="h-auto cursor-pointer p-0 text-xs text-muted-foreground hover:bg-transparent hover:text-foreground"
          onClick={() => setNotice(true)}
          type="button"
          variant="ghost"
        >
          Забыли пароль?
        </Button>
      ) : null}
      {notice ? (
        <p className="text-center text-xs text-muted-foreground">
          Скоро будет доступно
        </p>
      ) : null}
      {error ? (
        <p className="text-center text-xs text-destructive" role="alert">
          {error}
        </p>
      ) : null}
      <Button
        className="h-11 w-full bg-zinc-950 hover:bg-orange-600"
        disabled={pending}
        type="submit"
      >
        {pending ? "Подождите…" : mode === "login" ? "Войти" : "Создать аккаунт"}
      </Button>
    </form>
  )
}

export function AuthDialog({ open, onOpenChange }: AuthDialogProps) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [logoutError, setLogoutError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    let active = true
    getAuthState()
      .then((state) => {
        if (active) setUser(state.user)
      })
      .catch(() => undefined)
    return () => {
      active = false
    }
  }, [open])

  const handleLogout = async () => {
    setLogoutError(null)
    try {
      await logoutClient()
      setUser(null)
      onOpenChange(false)
    } catch (caught) {
      setLogoutError(
        caught instanceof Error ? caught.message : "Не удалось выйти из аккаунта"
      )
    }
  }

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="p-6 sm:max-w-[400px]">
        <DialogHeader className="items-center text-center">
          <div className="mb-2 grid size-11 place-items-center rounded-full bg-orange-500 text-white">
            <UserRound className="size-5" />
          </div>
          <DialogTitle className="text-xl">Личный кабинет</DialogTitle>
          <DialogDescription>
            История чата на разных устройствах
          </DialogDescription>
        </DialogHeader>
        {user ? (
          <div className="mt-3 space-y-4 text-center">
            <p className="text-sm">{user.name || user.email}</p>
            {logoutError ? (
              <p className="text-xs text-destructive" role="alert">
                {logoutError}
              </p>
            ) : null}
            <Button className="w-full" onClick={handleLogout} variant="outline">
              Выйти
            </Button>
          </div>
        ) : (
          <Tabs className="mt-2" defaultValue="login">
            <TabsList className="grid w-full grid-cols-2">
              <TabsTrigger value="login">Вход</TabsTrigger>
              <TabsTrigger value="register">Регистрация</TabsTrigger>
            </TabsList>
            <TabsContent value="login">
              <AuthForm
                mode="login"
                onSuccess={(nextUser) => {
                  setUser(nextUser)
                  onOpenChange(false)
                }}
              />
            </TabsContent>
            <TabsContent value="register">
              <AuthForm
                mode="register"
                onSuccess={(nextUser) => {
                  setUser(nextUser)
                  onOpenChange(false)
                }}
              />
            </TabsContent>
          </Tabs>
        )}
      </DialogContent>
    </Dialog>
  )
}
