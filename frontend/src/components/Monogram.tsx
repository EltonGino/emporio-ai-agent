/** Cream amplifier knob with an original, plain condensed monogram. */
export function Monogram({ size = 40, className = '' }: { size?: number; className?: string }) {
  return <svg width={size} height={size} viewBox="0 0 64 64" className={className}
    role="img" aria-label="Tom, assistente virtual">
    <circle cx="32" cy="32" r="31" fill="var(--color-maple)" />
    <circle cx="32" cy="32" r="27" fill="var(--color-cream)" stroke="var(--color-tolex)" />
    <path d="M32 7v6" stroke="var(--color-tolex)" strokeWidth="2" />
    <text x="32" y="44" textAnchor="middle" fontFamily="var(--font-display)" fontSize="32"
      fill="var(--color-tolex)">T</text>
  </svg>
}
