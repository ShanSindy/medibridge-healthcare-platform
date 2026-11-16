import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { cx, focusRing } from '../../lib/cx'
import { Icon } from '../ui/Icon'
import { SidebarNav } from './SidebarNav'
import { Topbar } from './Topbar'

interface AppShellProps {
  currentPath: string
  section: string
  pageLabel: string
  title: string
  description?: string
  children: ReactNode
}

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'

export function AppShell({ currentPath, section, pageLabel, title, description, children }: AppShellProps) {
  const [menuOpen, setMenuOpen] = useState(false)
  const menuButtonRef = useRef<HTMLButtonElement>(null)
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const mainRef = useRef<HTMLElement>(null)
  const previousPath = useRef(currentPath)

  // Move focus to the main region after client-side navigation so keyboard
  // and screen-reader users land on the new page content. Comparing paths
  // (rather than a "first render" flag) stays correct under StrictMode.
  useEffect(() => {
    if (previousPath.current === currentPath) return
    previousPath.current = currentPath
    mainRef.current?.focus()
    window.scrollTo({ top: 0 })
  }, [currentPath])

  const closeMenu = useCallback((restoreFocus: boolean) => {
    setMenuOpen(false)
    if (restoreFocus) menuButtonRef.current?.focus()
  }, [])

  // While the mobile drawer is open: focus it, lock page scroll, close on Escape.
  useEffect(() => {
    if (!menuOpen) return
    closeButtonRef.current?.focus()
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') closeMenu(true)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => {
      document.body.style.overflow = previousOverflow
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [menuOpen, closeMenu])

  // Close the drawer if the viewport grows to the desktop layout.
  useEffect(() => {
    const query = window.matchMedia('(min-width: 1024px)')
    const onChange = (event: MediaQueryListEvent) => {
      if (event.matches) setMenuOpen(false)
    }
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [])

  // Keep Tab focus inside the open drawer.
  const trapFocus = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'Tab') return
    const focusable = Array.from(event.currentTarget.querySelectorAll<HTMLElement>(FOCUSABLE))
    if (focusable.length === 0) return
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

  return (
    <div className="min-h-screen">
      {/* A button (not an anchor) so the skip link does not change the hash route. */}
      <button
        type="button"
        onClick={() => mainRef.current?.focus()}
        className="sr-only z-50 rounded-md bg-white px-4 py-2 text-sm font-medium text-navy-900 shadow focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:outline-2 focus:outline-brand-600"
      >
        Skip to main content
      </button>

      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 z-20 hidden w-64 border-r border-slate-200/80 bg-white lg:block">
        <SidebarNav currentPath={currentPath} />
      </aside>

      {/* Mobile navigation drawer */}
      {menuOpen && (
        <div
          id="mobile-navigation"
          role="dialog"
          aria-modal="true"
          aria-label="Navigation menu"
          className="fixed inset-0 z-40 lg:hidden"
          onKeyDown={trapFocus}
        >
          <div
            aria-hidden="true"
            className="absolute inset-0 bg-navy-950/40 backdrop-blur-[2px]"
            onClick={() => closeMenu(true)}
          />
          <div className="relative flex h-full w-72 max-w-[85vw] flex-col bg-white shadow-xl">
            <button
              ref={closeButtonRef}
              type="button"
              onClick={() => closeMenu(true)}
              className={cx(
                'absolute top-4 right-3 z-10 rounded-lg p-1.5 text-slate-500 hover:bg-slate-100 hover:text-navy-900',
                focusRing,
              )}
            >
              <Icon name="close" className="size-5" />
              <span className="sr-only">Close navigation menu</span>
            </button>
            <SidebarNav currentPath={currentPath} onNavigate={() => closeMenu(false)} />
          </div>
        </div>
      )}

      <div className="lg:pl-64">
        <Topbar
          section={section}
          pageLabel={pageLabel}
          menuOpen={menuOpen}
          onOpenMenu={() => setMenuOpen(true)}
          menuButtonRef={menuButtonRef}
        />
        <main
          ref={mainRef}
          id="main-content"
          tabIndex={-1}
          aria-labelledby="page-title"
          className="mx-auto max-w-7xl px-4 py-6 focus:outline-none sm:px-6 sm:py-8 lg:px-8"
        >
          <div className="mb-6 sm:mb-8">
            <h1 id="page-title" className="text-2xl font-semibold tracking-tight text-navy-900 sm:text-[26px]">
              {title}
            </h1>
            {description && <p className="mt-1 max-w-3xl text-sm text-slate-500 sm:text-[15px]">{description}</p>}
          </div>
          {children}
        </main>
      </div>
    </div>
  )
}
