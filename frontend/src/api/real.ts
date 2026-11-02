import { ApiError, type AgentEvent, type ApiClient, type AppConfig } from './types'

const SUGGESTIONS = [
  'Quais violões até R$ 1.000?',
  'Status do meu pedido',
  'Vocês vendem cordas?',
  'Me arrependi da compra',
]

async function errorFrom(response: Response): Promise<ApiError> {
  let message = 'Não consegui falar com o atendimento agora. Tente de novo em instantes.'
  try {
    const body = await response.json()
    if (typeof body?.detail === 'string') message = body.detail // e.g. 429 friendly message
  } catch {
    /* not JSON */
  }
  return new ApiError(response.status, message)
}

/** Parses `data: {...}` lines of a text/event-stream body. */
async function readEvents(body: ReadableStream<Uint8Array>, onEvent: (e: AgentEvent) => void) {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    // Normalize CRLF: some SSE servers (e.g. sse-starlette) separate lines with \r\n.
    buffer += decoder.decode(value, { stream: true }).replaceAll('\r\n', '\n')
    let end: number
    while ((end = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, end)
      buffer = buffer.slice(end + 2)
      const data = block
        .split('\n')
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).trimStart())
        .join('\n')
      if (data) onEvent(JSON.parse(data) as AgentEvent)
    }
  }
}

export function createRealClient(base = '/api'): ApiClient {
  return {
    mode: 'real',
    async getConfig() {
      const response = await fetch(`${base}/config`)
      if (!response.ok) throw await errorFrom(response)
      return (await response.json()) as AppConfig
    },
    async chat(sessionId, message, onEvent) {
      const response = await fetch(`${base}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: JSON.stringify({ session_id: sessionId, message }),
      })
      if (!response.ok || !response.body) throw await errorFrom(response)
      await readEvents(response.body, onEvent)
    },
    async deleteSession(sessionId) {
      const response = await fetch(`${base}/sessions/${encodeURIComponent(sessionId)}`, {
        method: 'DELETE',
      })
      if (!response.ok && response.status !== 404) throw await errorFrom(response)
    },
    suggestions: () => SUGGESTIONS,
  }
}
