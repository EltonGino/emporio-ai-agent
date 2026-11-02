import { useEffect, useRef } from 'react'
import type { AgentEvent, RetrievedChunk, TurnUsage } from '../api'
import type { Turn } from '../hooks/useChat'

/** The server-side duration from the final event (also right for replays), else measured here. */
function seconds(turn: Turn): string {
  const final = turn.events.find((e) => e.type === 'final')
  const ms = typeof final?.data.duration_ms === 'number' ? final.data.duration_ms : turn.durationMs
  return ((ms ?? 0) / 1000).toFixed(1)
}

const json = (value: unknown) => JSON.stringify(value, null, 1).replace(/\n\s*/g, ' ')

function Row({ mark, tone, children }: { mark: string; tone: string; children: React.ReactNode }) {
  return (
    <li className="flex gap-2">
      <span className={`w-4 shrink-0 text-center ${tone}`} aria-hidden="true">
        <span className="led mt-1.5" title={mark} />
      </span>
      <div className="min-w-0 flex-1 break-words">{children}</div>
    </li>
  )
}

function Notice({ data }: { data: Record<string, unknown> }) {
  const kind = String(data.kind)
  if (kind === 'rate_limit') {
    const action = data.action === 'wait' ? `espera ${data.retry_after}s e tenta de novo` : 'próximo modelo'
    return <Row mark="!" tone="text-console-amber">429 em {String(data.model)} → {action}</Row>
  }
  if (kind === 'invalid_tool_call') {
    const action = data.action === 'retry' ? 'nova tentativa' : 'próximo modelo'
    return <Row mark="!" tone="text-console-amber">chamada de ferramenta rejeitada ({String(data.model)}) → {action}</Row>
  }
  if (kind === 'output_guard') {
    const action = data.action === 'retry' ? 'resposta segurada, modelo corrigido' : 'resposta segura enviada'
    return (
      <Row mark="⛨" tone="text-console-red">
        guarda de saída: {json(data.failures)} → {action}
      </Row>
    )
  }
  if (kind === 'tool_artifact') {
    return (
      <Row mark="·" tone="text-console-dim">
        <span className="text-console-dim">{String(data.name)} (só no trace): {json(data.artifact)}</span>
      </Row>
    )
  }
  return <Row mark="·" tone="text-console-dim">{json(data)}</Row>
}

function EventRow({ event }: { event: AgentEvent }) {
  const data = event.data
  switch (event.type) {
    case 'tool_start':
      return (
        <Row mark="▸" tone="text-console-amber">
          <span className="text-console-text">{String(data.name)}</span>
          <span className="text-console-dim"> {json(data.args)}</span>
        </Row>
      )
    case 'tool_end':
      return (
        <Row mark="✓" tone={data.status === 'error' ? 'text-console-red' : 'text-console-green'}>
          <details>
            <summary className="cursor-pointer">
              {String(data.name)} <span className="text-console-dim">· {String(data.duration_ms)} ms</span>
            </summary>
            <p className="mt-1 text-console-dim">{String(data.result_preview)}</p>
          </details>
        </Row>
      )
    case 'retrieval':
      return (
        <Row mark="§" tone="text-console-green">
          <ul className="space-y-0.5">
            {(data.chunks as RetrievedChunk[]).map((chunk) => (
              <li key={chunk.section_id}>
                {chunk.section_path}
                <span className="text-console-dim">
                  {' '}
                  · score {chunk.score} · {chunk.matched_by.join('+')}
                </span>
              </li>
            ))}
          </ul>
        </Row>
      )
    case 'notice':
      return <Notice data={data} />
    case 'final': {
      const usage = data.usage as TurnUsage | undefined
      if (!usage) return null
      return (
        <Row mark="Σ" tone="text-console-dim">
          <span className="text-console-dim">
            tokens: entrada {usage.input_tokens} (cache {usage.cached_tokens}) · saída{' '}
            {usage.output_tokens} (raciocínio {usage.reasoning_tokens}) · {usage.model_calls}{' '}
            chamada(s) · {usage.models.join(', ') || '-'}
          </span>
        </Row>
      )
    }
    case 'error':
      return <Row mark="✗" tone="text-console-red">{String(data.message)}</Row>
    default:
      return null
  }
}

export function BackstagePanel({ turns, onClose }: { turns: Turn[]; onClose: () => void }) {
  const end = useRef<HTMLDivElement>(null)
  const eventCount = turns.reduce((n, t) => n + t.events.length, 0)
  useEffect(() => {
    // Braces matter: scrollIntoView returns a Promise in recent browsers, and an effect must not
    // return anything but a cleanup function.
    end.current?.scrollIntoView({ block: 'end' })
  }, [eventCount])

  return (
    <section
      aria-label="Bastidores"
      className="flex h-full flex-col bg-console font-mono text-[12.5px] leading-relaxed text-console-text"
    >
      <header className="flex items-start justify-between gap-3 border-b border-console-line px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold tracking-wide text-console-text">Bastidores · rack de efeitos</h2>
          <p className="mt-0.5 text-[11px] text-console-dim">
            Visível apenas no modo demo — clientes não veem este painel
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded border border-console-line px-2 py-0.5 text-console-dim hover:text-console-text"
          aria-label="Fechar bastidores"
        >
          ✕
        </button>
      </header>
      <div className="flex-1 space-y-4 overflow-y-auto px-4 py-3">
        {turns.length === 0 ? (
          <p className="text-console-dim">
            Aguardando a primeira mensagem… aqui aparecem as ferramentas chamadas, os trechos do
            manual recuperados, as decisões dos guardas e o consumo de tokens de cada turno.
          </p>
        ) : null}
        {turns.map((turn, index) => (
          <article key={turn.id} className="rounded border border-console-line">
            <header className="flex justify-between gap-2 border-b border-console-line bg-black/20 px-3 py-1.5">
              <span className="truncate">
                <span className="text-console-dim">#{index + 1} </span>
                {turn.user}
              </span>
              {turn.durationMs !== undefined ? (
                <span className="shrink-0 text-console-dim">{seconds(turn)} s</span>
              ) : (
                <span className="shrink-0 animate-pulse text-console-text">…</span>
              )}
            </header>
            <ol className="space-y-1.5 px-3 py-2">
              {turn.events.map((event, i) => (
                <EventRow key={i} event={event} />
              ))}
              {turn.events.length === 1 && turn.events[0].type === 'final' ? (
                <Row mark="·" tone="text-console-dim">
                  <span className="text-console-dim">sem ferramentas: resposta direta do modelo</span>
                </Row>
              ) : null}
            </ol>
          </article>
        ))}
        <div ref={end} />
      </div>
    </section>
  )
}
