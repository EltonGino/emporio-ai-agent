/** Shown when the server runs with a frozen clock (AGENT_NOW): dates in answers depend on it. */
export function SimulatedDateBanner({ iso }: { iso: string | null | undefined }) {
  const match = iso?.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/)
  if (!match) return null
  const [, year, month, day, hour, minute] = match
  return (
    <div
      role="note"
      className="border-b border-amber/40 bg-spruce px-4 py-1.5 text-center text-sm text-walnut"
    >
      <span aria-hidden="true">🕰 </span>
      Data simulada: <strong className="font-semibold">{`${day}/${month}/${year} ${hour}:${minute}`}</strong>{' '}
      (horário de Campo Grande)
    </div>
  )
}
