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
  'flex-1 rounded-full border border-maple/60 px-3 py-1.5 text-sm font-semibold whitespace-nowrap ' +
  'text-ivory transition hover:bg-ivory/10 disabled:opacity-50 sm:flex-none sm:px-4'

export function Header(props: Props) {
  return (
    <header className="wood-sign relative z-10 text-ivory shadow-md">
      <div className="flex flex-col gap-2.5 px-4 py-3 sm:flex-row sm:items-center sm:gap-4 sm:px-6">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <Monogram size={40} className="shrink-0 drop-shadow sm:size-11" />
          <div className="min-w-0">
            <h1 className="truncate font-display text-xl leading-tight font-semibold tracking-tight sm:text-2xl">
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
        <nav className="flex items-center gap-2" aria-label="Ações da conversa">
          <button type="button" className={buttonClass} onClick={props.onNewConversation} disabled={props.busy}>
            Nova conversa
          </button>
          <button
            type="button"
            className={buttonClass}
            onClick={props.onDeleteData}
            disabled={props.busy}
            aria-label="Apagar meus dados"
          >
            <span className="sm:hidden">Apagar dados</span>
            <span className="hidden sm:inline">Apagar meus dados</span>
          </button>
          {props.extraActions}
        </nav>
      </div>
    </header>
  )
}
