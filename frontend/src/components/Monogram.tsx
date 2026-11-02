/** Tom's avatar: a serif "T" inside rings, like a guitar's soundhole rosette. */
export function Monogram({ size = 40, className = '' }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      className={className}
      role="img"
      aria-label="Tom, assistente virtual"
    >
      <circle cx="32" cy="32" r="31" fill="var(--color-burgundy)" />
      <circle cx="32" cy="32" r="27" fill="none" stroke="var(--color-maple)" strokeWidth="1.5" />
      <circle
        cx="32"
        cy="32"
        r="24.5"
        fill="none"
        stroke="var(--color-amber)"
        strokeWidth="1"
        strokeDasharray="2 2.6"
      />
      <text
        x="32"
        y="42"
        textAnchor="middle"
        fontFamily="var(--font-display)"
        fontSize="28"
        fontStyle="italic"
        fill="var(--color-ivory)"
      >
        T
      </text>
    </svg>
  )
}
