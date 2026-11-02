import { useEffect, useRef } from 'react'
import Markdown from 'react-markdown'
import type { ChatMessage } from '../hooks/useChat'
import { Monogram } from './Monogram'

function TypingDots({ name }: { name: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-walnut-soft" role="status">
      <span className="inline-flex gap-1" aria-hidden="true">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="size-2 animate-bounce rounded-full bg-amber"
            style={{ animationDelay: `${i * 150}ms` }}
          />
        ))}
      </span>
      <span className="text-sm">{name} está digitando…</span>
    </span>
  )
}

function Bubble({ message, assistantName }: { message: ChatMessage; assistantName: string }) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <p className="max-w-[85%] rounded-2xl rounded-br-sm bg-tolex px-4 py-2.5 whitespace-pre-wrap text-ivory shadow-sm sm:max-w-[75%]">
          {message.text}
        </p>
      </div>
    )
  }
  const waiting = message.status === 'streaming' && !message.text
  return (
    <div className="flex items-end gap-2.5">
      <Monogram size={32} className="mb-1 shrink-0" />
      <div
        className={
          'chat-markdown min-w-0 max-w-[85%] rounded-2xl rounded-bl-sm border px-4 py-2.5 shadow-sm sm:max-w-[75%] ' +
          (message.status === 'error'
            ? 'border-burgundy/40 bg-[#fbe9e4] text-burgundy-deep'
            : 'border-maple bg-cream text-walnut')
        }
      >
        {waiting ? <TypingDots name={assistantName} /> : <Markdown>{message.text}</Markdown>}
        {message.status === 'done' && message.products?.length ? (
          <div className="mt-4 grid min-w-0 gap-2" aria-label="Produtos citados">
            {message.products.map((p) => <article className="product-card" key={p.id}>
              <h3 className="break-words font-display text-xl">{p.name}</h3>
              {p.promo ? <p className="mt-1 text-sm text-walnut-soft">
                <s>{p.price_text}</s> <span className="ml-2 font-semibold text-burgundy">{p.promo.percent}% de desconto</span>
              </p> : null}
              <p className="product-price mt-1 text-2xl">{p.promo?.final_price_text ?? p.price_text}</p>
              {p.pix_price_text ? <p className="text-sm text-burgundy">{p.pix_price_text} no PIX</p> : null}
              <p className="mt-2 flex items-center gap-2 text-sm text-walnut-soft">
                <span
                  className={`led ${p.availability === 'disponivel' ? 'text-console-green'
                    : p.availability === 'esgotado' ? 'text-amber' : 'led-off'}`}
                  aria-hidden="true"
                />
                {{ disponivel: 'Disponível', esgotado: 'Temporariamente indisponível',
                  descontinuado: 'Fora de catálogo', em_breve: 'Chega em breve' }[p.availability] ?? p.availability}
              </p>
            </article>)}
          </div>
        ) : null}
      </div>
    </div>
  )
}

export function MessageList({
  messages,
  assistantName,
  empty,
}: {
  messages: ChatMessage[]
  assistantName: string
  empty: React.ReactNode
}) {
  const end = useRef<HTMLDivElement>(null)
  const last = messages.at(-1)
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages.length, last?.text, last?.products])

  return (
    <div className="flex-1 overflow-y-auto" aria-live="polite" aria-relevant="additions text">
      <div className="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-6 sm:px-6">
        {messages.length === 0 ? empty : null}
        {messages.map((m) => (
          <Bubble key={m.id} message={m} assistantName={assistantName} />
        ))}
        <div ref={end} />
      </div>
    </div>
  )
}
