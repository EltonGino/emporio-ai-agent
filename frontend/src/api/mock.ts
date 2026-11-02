// Mock API for VITE_MOCK=true: replays real agent conversations recorded in JSONL traces
// (exported and anonymized by scripts/export_demo_fixtures.py). No backend needed.
import recorded from '../mocks/conversations.json'
import type { AgentEvent, ApiClient, AppConfig } from './types'

interface RecordedTurn {
  user: string
  events: AgentEvent[]
}
interface RecordedConversation {
  id: string
  title: string
  now: string
  turns: RecordedTurn[]
}

export const conversations = (recorded as { conversations: RecordedConversation[] })
  .conversations

const normalize = (text: string) =>
  text.normalize('NFD').replace(/\p{Diacritic}/gu, '').toLowerCase().replace(/\W+/g, ' ').trim()

function findTurn(message: string): { conversation: RecordedConversation; index: number } | null {
  const wanted = normalize(message)
  for (const conversation of conversations) {
    const index = conversation.turns.findIndex((t) => normalize(t.user) === wanted)
    if (index >= 0) return { conversation, index }
  }
  return null
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

const FALLBACK =
  'Este é o **modo demonstração**: eu repito conversas reais gravadas com o agente. ' +
  'Escolha uma das sugestões abaixo para ver como eu respondo.'

/** Delays imitate the real pace; `instant` replays with none (used by ?replay=). */
export function createMockClient(options: { instant?: boolean; replay?: string } = {}): ApiClient {
  // A replayed conversation shows the simulated date it was recorded at.
  const recordedNow = conversations.find((c) => c.id === options.replay)?.now
  const pause = (ms: number) => (options.instant ? Promise.resolve() : sleep(ms))
  return {
    mode: 'mock',
    async getConfig(): Promise<AppConfig> {
      return {
        assistant_name: 'Tom',
        store_name: 'Empório da Música',
        slogan: 'Sua música começa aqui.',
        demo_mode: true,
        simulated_now: recordedNow ?? '2026-03-25T10:30',
      }
    },
    async chat(_sessionId, message, onEvent) {
      const found = findTurn(message)
      const events = found
        ? found.conversation.turns[found.index].events
        : [{ type: 'final', data: { text: FALLBACK } } as AgentEvent]
      await pause(450)
      for (const event of events) {
        if (event.type === 'final') {
          // Traces don't store token events: replay the validated answer word by word.
          const text = String(event.data.text ?? '')
          for (const piece of text.match(/\S+\s*|\s+/g) ?? []) {
            onEvent({ type: 'token', data: { text: piece } })
            await pause(18)
          }
        } else {
          await pause(event.type === 'tool_start' ? 350 : 250)
        }
        onEvent(event)
      }
    },
    async deleteSession() {
      await pause(300)
    },
    suggestions(lastMessage) {
      const found = lastMessage ? findTurn(lastMessage) : null
      if (!found) return conversations.map((c) => c.turns[0].user)
      // Next turn of the conversation being replayed, then the other conversations' openers.
      const next = found.conversation.turns[found.index + 1]
      const others = conversations
        .filter((c) => c.id !== found.conversation.id)
        .map((c) => c.turns[0].user)
      return next ? [next.user, ...others] : others
    },
  }
}
