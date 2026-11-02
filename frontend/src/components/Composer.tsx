import { useState } from 'react'

export function Composer({ busy, onSend }: { busy: boolean; onSend: (text: string) => void }) {
  const [text, setText] = useState('')
  const submit = () => {
    if (!text.trim() || busy) return
    onSend(text)
    setText('')
  }
  return (
    <form
      className="flex items-end gap-2"
      onSubmit={(event) => {
        event.preventDefault()
        submit()
      }}
    >
      <label htmlFor="message" className="sr-only">
        Sua mensagem
      </label>
      <textarea
        id="message"
        rows={1}
        value={text}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault()
            submit()
          }
        }}
        placeholder="Escreva sua mensagem…"
        maxLength={1000}
        className="max-h-40 min-h-12 flex-1 resize-none rounded-2xl border border-maple bg-cream px-4 py-3 text-walnut placeholder:text-walnut-soft/70 focus:border-amber"
      />
      <button
        type="submit"
        disabled={busy || !text.trim()}
        className="h-12 rounded-2xl bg-amber-deep px-5 font-semibold text-cream shadow-sm transition hover:bg-burgundy disabled:cursor-not-allowed disabled:opacity-50"
      >
        Enviar
      </button>
    </form>
  )
}
