import { useEffect, useMemo, useRef, useState } from 'react'
import { createApiClient, isMock } from './api'
import { conversations } from './api/mock'
import { BackstagePanel } from './components/BackstagePanel'
import { Composer } from './components/Composer'
import { Header } from './components/Header'
import { MessageList } from './components/MessageList'
import { Monogram } from './components/Monogram'
import { SimulatedDateBanner } from './components/SimulatedDateBanner'
import { SuggestionChips } from './components/SuggestionChips'
import { useChat } from './hooks/useChat'

const params = new URLSearchParams(location.search)
// Mock mode only: ?replay=<conversation id> plays a recorded conversation at once (screenshots).
const replayId = isMock ? params.get('replay') : null
const DESKTOP = '(min-width: 1024px)'

export default function App() {
  const client = useMemo(
    () => createApiClient({ instant: Boolean(replayId), replay: replayId ?? undefined }),
    [],
  )
  const chat = useChat(client)
  const { config, send } = chat
  const assistant = config?.assistant_name ?? 'Tom'
  const demo = config?.demo_mode ?? false
  // Bastidores: open by default on desktop, a closed drawer on phones (?panel=open forces it).
  const [panelOpen, setPanelOpen] = useState(
    () => params.get('panel') === 'open' || window.matchMedia(DESKTOP).matches,
  )

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
      <h2 className="mt-4 font-display text-3xl text-burgundy">Sua música começa aqui.</h2>
      <p className="mt-2 text-walnut-soft">
        Eu sou o {assistant}, assistente virtual do {config?.store_name ?? 'Empório da Música'}. Começando agora ou já toca? Posso ajudar com
        instrumentos, preços, formas de pagamento, pedidos, trocas e horários da loja.
      </p>
    </div>
  )

  return (
    <div className="flex h-full flex-col">
      <Header
        storeStatus={config?.store_status}
        storeName={config?.store_name ?? 'Empório da Música'}
        slogan={config?.slogan ?? 'Sua música começa aqui.'}
        assistantName={assistant}
        busy={chat.busy}
        onNewConversation={chat.newConversation}
        onDeleteData={confirmDelete}
        extraActions={
          demo ? (
            <button
              type="button"
              aria-pressed={panelOpen}
              onClick={() => setPanelOpen((open) => !open)}
              className="flex-1 rounded-full bg-console px-2.5 py-1.5 font-mono text-xs whitespace-nowrap text-console-text ring-1 ring-console-amber/40 transition hover:ring-console-amber sm:flex-none sm:px-4 sm:text-sm"
            >
              <span className="sm:hidden">Bastidores</span>
              <span className="hidden sm:inline">{panelOpen ? 'Ocultar bastidores' : 'Bastidores'}</span>
            </button>
          ) : null
        }
      />
      <SimulatedDateBanner iso={config?.simulated_now} />
      <div className="flex min-h-0 flex-1">
      <main className="flex min-h-0 min-w-0 flex-1 flex-col">
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
      {demo && panelOpen ? (
        <>
          {/* Phones: a drawer over the chat, with a backdrop; desktop: a side column. */}
          <div
            className="fixed inset-0 z-20 bg-walnut/50 lg:hidden"
            onClick={() => setPanelOpen(false)}
            aria-hidden="true"
          />
          <aside className="fixed inset-y-0 right-0 z-30 w-[min(92vw,26rem)] shadow-2xl lg:static lg:z-auto lg:w-[28rem] lg:shadow-none">
            <BackstagePanel turns={chat.turns} onClose={() => setPanelOpen(false)} />
          </aside>
        </>
      ) : null}
      </div>
    </div>
  )
}
