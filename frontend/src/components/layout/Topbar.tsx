import type { RefObject } from 'react'
import { cx, focusRing } from '../../lib/cx'
import { Badge } from '../ui/Badge'
import { Icon } from '../ui/Icon'
import { Avatar } from '../ui/Misc'
import { BrandMark } from './Brand'

interface TopbarProps {
  section: string
  pageLabel: string
  onOpenMenu: () => void
  menuOpen: boolean
  menuButtonRef: RefObject<HTMLButtonElement | null>
}

export function Topbar({ section, pageLabel, onOpenMenu, menuOpen, menuButtonRef }: TopbarProps) {
  return (
    <header className="sticky top-0 z-30 border-b border-slate-200/80 bg-white/90 backdrop-blur supports-[backdrop-filter]:bg-white/75">
      <div className="flex h-16 items-center gap-3 px-4 sm:px-6 lg:px-8">
        <button
          ref={menuButtonRef}
          type="button"
          onClick={onOpenMenu}
          aria-expanded={menuOpen}
          aria-controls="mobile-navigation"
          className={cx('-ml-1 rounded-lg p-2 text-navy-700 hover:bg-slate-100 lg:hidden', focusRing)}
        >
          <Icon name="menu" className="size-5" />
          <span className="sr-only">Open navigation menu</span>
        </button>

        <BrandMark className="size-7 lg:hidden" />

        <nav aria-label="Breadcrumb" className="min-w-0 flex-1">
          <ol className="flex items-center gap-1.5 text-sm">
            <li className="hidden text-slate-500 sm:block">{section}</li>
            <li aria-hidden="true" className="hidden text-slate-300 sm:block">
              /
            </li>
            <li aria-current="page" className="truncate font-medium text-navy-900">
              {pageLabel}
            </li>
          </ol>
        </nav>

        <div className="flex items-center gap-3">
          <span className="hidden md:inline-flex">
            <Badge tone="warning" dot>
              Demo data · Not connected to API
            </Badge>
          </span>
          <span className="md:hidden">
            <Badge tone="warning" dot>
              Demo
            </Badge>
          </span>
          <div className="flex items-center gap-2.5 border-l border-slate-200 pl-3">
            <Avatar initials="DA" size="sm" />
            <div className="hidden leading-tight sm:block">
              <p className="text-sm font-medium text-navy-900">Demo Administrator</p>
              <p className="text-xs text-slate-500">Sign-in not enabled</p>
            </div>
          </div>
        </div>
      </div>
    </header>
  )
}
