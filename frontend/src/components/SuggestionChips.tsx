export function SuggestionChips({
  suggestions,
  disabled,
  onPick,
}: {
  suggestions: string[]
  disabled: boolean
  onPick: (text: string) => void
}) {
  if (suggestions.length === 0) return null
  return (
    <div className="flex gap-2 overflow-x-auto pb-1 sm:flex-wrap" aria-label="Sugestões">
      {suggestions.slice(0, 5).map((s) => (
        <button
          key={s}
          type="button"
          disabled={disabled}
          onClick={() => onPick(s)}
          className="shrink-0 rounded-full border border-maple bg-tolex px-3 py-1.5 text-sm text-cream transition hover:border-maple hover:bg-walnut disabled:opacity-50"
        >
          {s}
        </button>
      ))}
    </div>
  )
}
