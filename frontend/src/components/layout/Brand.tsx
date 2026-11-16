import { useId } from 'react'

export function BrandMark({ className = 'size-8' }: { className?: string }) {
  // Unique per instance: the mark renders in several places at once.
  const gradientId = `mb-gradient-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`
  return (
    <svg viewBox="0 0 32 32" fill="none" className={className} aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="32" y2="32" gradientUnits="userSpaceOnUse">
          <stop stopColor="#1c2943" />
          <stop offset="1" stopColor="#157a77" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="8" fill={`url(#${gradientId})`} />
      <path d="M16 9v14M9 16h14" stroke="#fff" strokeWidth="3" strokeLinecap="round" />
      <path
        d="M7 24c2.5-2.6 5.5-3.9 9-3.9s6.5 1.3 9 3.9"
        stroke="#77cfc8"
        strokeWidth="1.75"
        strokeLinecap="round"
      />
    </svg>
  )
}

export function BrandLockup() {
  return (
    <span className="flex items-center gap-2.5">
      <BrandMark />
      <span className="flex flex-col leading-tight">
        <span className="text-[15px] font-semibold tracking-tight text-navy-900">MediBridge</span>
        <span className="text-[11px] font-medium tracking-wide text-slate-500 uppercase">Healthcare Platform</span>
      </span>
    </span>
  )
}
