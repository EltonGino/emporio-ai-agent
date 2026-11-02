// Contract with the backend: AgentEvent (src/emporio_agent/core/events.py) and the
// endpoints of AGENTS.md §11. The FastAPI app (step 17) must match these types.

export type EventType =
  | 'user_message'
  | 'tool_start'
  | 'tool_end'
  | 'retrieval'
  | 'notice'
  | 'token'
  | 'final'
  | 'error'

export interface AgentEvent {
  type: EventType
  session_id?: string
  at?: string
  data: Record<string, unknown>
}

export interface TurnUsage {
  input_tokens: number
  cached_tokens: number
  output_tokens: number
  reasoning_tokens: number
  model_calls: number
  models: string[]
}

export interface RetrievedChunk {
  section_id: string
  section_path: string
  page?: number
  score: number
  matched_by: string[]
}

/** GET /api/config */
export interface AppConfig {
  assistant_name: string
  store_name: string
  slogan: string
  demo_mode: boolean
  simulated_now: string | null // ISO, store time
}

export class ApiError extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export interface ApiClient {
  readonly mode: 'real' | 'mock'
  getConfig(): Promise<AppConfig>
  /** POST /api/chat → SSE stream of AgentEvents; resolves when the stream ends. */
  chat(sessionId: string, message: string, onEvent: (event: AgentEvent) => void): Promise<void>
  /** DELETE /api/sessions/{id} (LGPD: conversation and trace). */
  deleteSession(sessionId: string): Promise<void>
  /** Prompt chips to show, given the customer's last message (if any). */
  suggestions(lastMessage?: string): string[]
}
