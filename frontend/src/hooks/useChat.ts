import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, type AgentEvent, type ApiClient, type AppConfig } from '../api'

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  status: 'streaming' | 'done' | 'error'
}

/** One customer message and everything the agent did for it (for the Bastidores panel). */
export interface Turn {
  id: string
  user: string
  events: AgentEvent[] // tool_*, retrieval, notice, final, error (no tokens)
  startedAt: number
  durationMs?: number
}

const SESSION_KEY = 'emporio.session_id'
const newId = () => crypto.randomUUID().replaceAll('-', '') // [A-Za-z0-9_-], like the API

function storedSession(): string {
  try {
    const existing = localStorage.getItem(SESSION_KEY)
    if (existing) return existing
  } catch {
    /* storage unavailable: fall through */
  }
  return rememberSession(newId())
}

function rememberSession(id: string): string {
  try {
    localStorage.setItem(SESSION_KEY, id)
  } catch {
    /* storage unavailable: the id lives only in memory */
  }
  return id
}

export function useChat(client: ApiClient) {
  const [config, setConfig] = useState<AppConfig | null>(null)
  const [configError, setConfigError] = useState<string | null>(null)
  const [sessionId, setSessionId] = useState(storedSession)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [turns, setTurns] = useState<Turn[]>([])
  const [busy, setBusy] = useState(false)
  const busyRef = useRef(false)

  useEffect(() => {
    client
      .getConfig()
      .then(setConfig)
      .catch((error: unknown) =>
        setConfigError(error instanceof Error ? error.message : 'Falha ao carregar.'),
      )
  }, [client])

  const send = useCallback(
    async (text: string) => {
      const message = text.trim()
      if (!message || busyRef.current) return
      busyRef.current = true
      setBusy(true)
      const answerId = newId()
      const turnId = newId()
      const startedAt = performance.now()
      setMessages((list) => [
        ...list,
        { id: newId(), role: 'user', text: message, status: 'done' },
        { id: answerId, role: 'assistant', text: '', status: 'streaming' },
      ])
      setTurns((list) => [...list, { id: turnId, user: message, events: [], startedAt }])

      const patchAnswer = (patch: (m: ChatMessage) => ChatMessage) =>
        setMessages((list) => list.map((m) => (m.id === answerId ? patch(m) : m)))
      const addTurnEvent = (event: AgentEvent) =>
        setTurns((list) =>
          list.map((t) => (t.id === turnId ? { ...t, events: [...t.events, event] } : t)),
        )

      const onEvent = (event: AgentEvent) => {
        switch (event.type) {
          case 'token':
            patchAnswer((m) => ({ ...m, text: m.text + String(event.data.text ?? '') }))
            return
          case 'final':
            patchAnswer((m) => ({ ...m, text: String(event.data.text ?? ''), status: 'done' }))
            break
          case 'error':
            patchAnswer((m) => ({ ...m, text: String(event.data.message ?? ''), status: 'error' }))
            break
          case 'user_message':
            return
        }
        addTurnEvent(event)
      }

      try {
        await client.chat(sessionId, message, onEvent)
      } catch (error) {
        const text =
          error instanceof ApiError
            ? error.message
            : 'Não consegui falar com o atendimento agora. Tente de novo em instantes.'
        patchAnswer((m) => ({ ...m, text, status: 'error' }))
      } finally {
        const durationMs = Math.round(performance.now() - startedAt)
        setTurns((list) => list.map((t) => (t.id === turnId ? { ...t, durationMs } : t)))
        patchAnswer((m) => (m.status === 'streaming' ? { ...m, status: 'done' } : m))
        busyRef.current = false
        setBusy(false)
      }
    },
    [client, sessionId],
  )

  const newConversation = useCallback(() => {
    setSessionId(rememberSession(newId()))
    setMessages([])
    setTurns([])
  }, [])

  /** LGPD: deletes this conversation on the server, then starts a fresh one. */
  const deleteMyData = useCallback(async () => {
    await client.deleteSession(sessionId)
    newConversation()
  }, [client, sessionId, newConversation])

  const lastUser = [...messages].reverse().find((m) => m.role === 'user')?.text
  const suggestions = useMemo(() => client.suggestions(lastUser), [client, lastUser])

  return {
    config,
    configError,
    sessionId,
    messages,
    turns,
    busy,
    suggestions,
    send,
    newConversation,
    deleteMyData,
  }
}
