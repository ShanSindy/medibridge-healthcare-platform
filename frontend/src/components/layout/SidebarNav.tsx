import { cx, focusRing } from '../../lib/cx'
import { NAV_SECTIONS, ROUTES, routeHref } from '../../routes'
import { Icon } from '../ui/Icon'
import { BrandLockup } from './Brand'

interface SidebarNavProps {
  currentPath: string
  /** Called after a link is activated (used to close the mobile drawer). */
  onNavigate?: () => void
}

export function SidebarNav({ currentPath, onNavigate }: SidebarNavProps) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-16 shrink-0 items-center px-5">
        <a href={routeHref('/')} onClick={onNavigate} className={cx('rounded-md', focusRing)}>
          <BrandLockup />
          <span className="sr-only"> — go to overview</span>
        </a>
      </div>

      <nav aria-label="Primary" className="flex-1 overflow-y-auto px-3 pt-2 pb-4">
        {NAV_SECTIONS.map((section) => (
          <div key={section} className="mb-5">
            <h2 className="px-3 pb-1.5 text-[11px] font-semibold tracking-wider text-slate-400 uppercase">
              {section}
            </h2>
            <ul className="space-y-0.5">
              {ROUTES.filter((route) => route.section === section).map((route) => {
                const active = route.path === currentPath
                return (
                  <li key={route.id}>
                    <a
                      href={routeHref(route.path)}
                      onClick={onNavigate}
                      aria-current={active ? 'page' : undefined}
                      className={cx(
                        'group relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
                        focusRing,
                        active
                          ? 'bg-brand-50 text-brand-800'
                          : 'text-navy-600 hover:bg-slate-100 hover:text-navy-900',
                      )}
                    >
                      {active && (
                        <span
                          aria-hidden="true"
                          className="absolute inset-y-1.5 left-0 w-0.5 rounded-full bg-brand-600"
                        />
                      )}
                      <Icon
                        name={route.icon}
                        className={cx(
                          'size-[18px] shrink-0',
                          active ? 'text-brand-700' : 'text-slate-400 group-hover:text-navy-600',
                        )}
                      />
                      {route.label}
                    </a>
                  </li>
                )
              })}
            </ul>
          </div>
        ))}
      </nav>

      <div className="m-3 rounded-lg border border-amber-200 bg-amber-50/70 p-3 text-xs text-amber-900">
        <p className="font-semibold">Demo environment</p>
        <p className="mt-0.5 text-amber-800">
          All records are synthetic. No real patient information is shown or stored.
        </p>
      </div>
    </div>
  )
}
