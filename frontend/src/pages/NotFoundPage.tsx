import { Card } from '../components/ui/Card'
import { EmptyState } from '../components/ui/States'
import { focusRing } from '../lib/cx'
import { routeHref } from '../routes'

export function NotFoundPage() {
  return (
    <Card>
      <EmptyState
        icon="search"
        title="We couldn’t find that page"
        description="The address may be mistyped, or the page may have moved."
        action={
          <a
            href={routeHref('/')}
            className={`inline-flex items-center rounded-lg bg-brand-600 px-3.5 py-2 text-sm font-medium text-white shadow-sm hover:bg-brand-700 ${focusRing}`}
          >
            Back to dashboard
          </a>
        }
      />
    </Card>
  )
}
