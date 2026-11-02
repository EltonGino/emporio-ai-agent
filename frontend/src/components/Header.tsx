import type { AppConfig } from '../api'
import { Monogram } from './Monogram'

interface Props {
  storeStatus?: AppConfig['store_status']
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
    <header className="tolex-header relative z-10 text-ivory shadow-md">
      <div className="flex flex-col gap-2.5 px-4 py-3 sm:flex-row sm:items-center sm:gap-4 sm:px-6">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <Monogram size={40} className="shrink-0 drop-shadow sm:size-11" />
          <div className="min-w-0">
            <h1 className="truncate font-display text-xl leading-tight font-semibold tracking-tight sm:text-2xl">
              {props.storeName}
            </h1>
            <p className="mt-1 flex items-center gap-2 text-sm text-maple">
              <span className="led text-amber" aria-hidden="true" />
              {statusLabel(props.storeStatus)}
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

function statusLabel(status: AppConfig['store_status']): string {
  if (!status) return 'Atendimento virtual · Sua música começa aqui.'
  const hour = (value: string) => value.replace(':00', 'h').replace(/^(0)(\d)/, '$2')
  if (status.is_open) return `Loja aberta · fecha às ${hour(status.closes_at ?? '')}`
  if (!status.next_opening) return 'Loja fechada'
  const day = status.next_opening.split(' ')[0]
  const time = status.next_opening.split(' ').at(-1) ?? ''
  const short: Record<string, string> = {
    'segunda-feira,': 'seg.', 'terça-feira,': 'ter.', 'quarta-feira,': 'qua.',
    'quinta-feira,': 'qui.', 'sexta-feira,': 'sex.', 'sábado,': 'sáb.', 'domingo,': 'dom.',
  }
  return `Loja fechada · abre ${short[day] ?? day} às ${hour(time)}`
}
