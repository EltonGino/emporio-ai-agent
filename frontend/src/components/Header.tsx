import { Monogram } from './Monogram'

interface Props {
  storeName: string
  slogan: string
  assistantName: string
  busy: boolean
  onNewConversation: () => void
  onDeleteData: () => void
  extraActions?: React.ReactNode
}

const buttonClass =
  'rounded-full border border-maple/60 px-3 py-1.5 text-sm font-semibold text-ivory ' +
  'transition hover:bg-ivory/10 disabled:opacity-50 sm:px-4'

export function Header(props: Props) {
  return (
    <header className="wood-sign relative z-10 text-ivory shadow-md">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 sm:px-6">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <Monogram size={44} className="shrink-0 drop-shadow" />
          <div className="min-w-0">
            <h1 className="font-display text-xl leading-tight font-semibold tracking-tight sm:text-2xl">
              {props.storeName}
            </h1>
            <p className="truncate text-sm text-maple">
              <span className="italic">{props.slogan}</span>
              <span className="hidden sm:inline">
                {' '}
                · Atendimento com o {props.assistantName}, assistente virtual
              </span>
            </p>
          </div>
        </div>
        <nav className="flex flex-wrap items-center gap-2" aria-label="Ações da conversa">
          <button type="button" className={buttonClass} onClick={props.onNewConversation} disabled={props.busy}>
            Nova conversa
          </button>
          <button type="button" className={buttonClass} onClick={props.onDeleteData} disabled={props.busy}>
            Apagar meus dados
          </button>
          {props.extraActions}
        </nav>
      </div>
    </header>
  )
}
