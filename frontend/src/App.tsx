import { useEffect, useMemo, useRef } from 'react'
import { createApiClient, isMock } from './api'
import { conversations } from './api/mock'
import { Composer } from './components/Composer'
import { Header } from './components/Header'
import { MessageList } from './components/MessageList'
import { Monogram } from './components/Monogram'
import { SuggestionChips } from './components/SuggestionChips'
import { useChat } from './hooks/useChat'

// Mock mode only: ?replay=<conversation id> plays a recorded conversation at once (screenshots).
const replayId = isMock ? new URLSearchParams(location.search).get('replay') : null

export default function App() {
  const client = useMemo(() => createApiClient({ instant: Boolean(replayId) }), [])
  const chat = useChat(client)
  const { config, send } = chat
  const assistant = config?.assistant_name ?? 'Tom'

  const replayed = useRef(false)
  useEffect(() => {
    const recorded = conversations.find((c) => c.id === replayId)
    if (!config || !recorded || replayed.current) return
    replayed.current = true
    void (async () => {
      for (const turn of recorded.turns) await send(turn.user)
    })()
  }, [config, send])

  const confirmDelete = () => {
    if (window.confirm('Apagar esta conversa e os dados dela? Isso não pode ser desfeito.')) {
      void chat.deleteMyData()
    }
  }

  if (chat.configError) {
    return (
      <main className="flex h-full items-center justify-center p-6 text-center">
        <p>Não consegui abrir o atendimento agora ({chat.configError}). Tente recarregar a página.</p>
      </main>
    )
  }

  const welcome = (
    <div className="mx-auto mt-6 max-w-xl text-center sm:mt-12">
      <Monogram size={72} className="mx-auto" />
      <h2 className="mt-4 font-display text-3xl text-burgundy">Oi! Eu sou o {assistant}.</h2>
      <p className="mt-2 text-walnut-soft">
        Assistente virtual do {config?.store_name ?? 'Empório da Música'}. Posso ajudar com
        instrumentos, preços, formas de pagamento, pedidos, trocas e horários da loja.
      </p>
    </div>
  )

  return (
    <div className="flex h-full flex-col">
      <Header
        storeName={config?.store_name ?? 'Empório da Música'}
        slogan={config?.slogan ?? 'Sua música começa aqui.'}
        assistantName={assistant}
        busy={chat.busy}
        onNewConversation={chat.newConversation}
        onDeleteData={confirmDelete}
      />
      <main className="flex min-h-0 flex-1 flex-col">
        <MessageList messages={chat.messages} assistantName={assistant} empty={welcome} />
        <div className="border-t border-maple/70 bg-ivory/95">
          <div className="mx-auto flex max-w-3xl flex-col gap-3 px-4 py-3 sm:px-6">
            <SuggestionChips suggestions={chat.suggestions} disabled={chat.busy} onPick={send} />
            <Composer busy={chat.busy} onSend={send} />
            <p className="text-center text-xs text-walnut-soft">
              {assistant} é um assistente virtual{isMock ? ' · modo demonstração' : ''}.
            </p>
          </div>
        </div>
      </main>
    </div>
  )
}
