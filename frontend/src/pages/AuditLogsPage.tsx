import { Card } from '../components/ui/Card'
import { Icon } from '../components/ui/Icon'
import { Callout } from '../components/ui/Misc'
import { EmptyState } from '../components/ui/States'

const EVENT_FIELDS = [
  { field: 'action', description: 'What happened, e.g. a record was created, read or updated.' },
  { field: 'resource_type', description: 'The kind of record affected, such as a patient or appointment.' },
  { field: 'resource_id', description: 'Identifier of the affected record, when applicable.' },
  { field: 'outcome', description: 'Either success or failure.' },
  { field: 'user_id', description: 'The authenticated user who performed the action.' },
  { field: 'details', description: 'Supporting context. Never contains passwords, tokens or patient data.' },
]

const TABLE_HEADERS = ['Timestamp', 'User', 'Action', 'Resource', 'Outcome']

const PLANNED = [
  'Paginated event history using the endpoint’s limit and offset parameters.',
  'Filters for action, resource type, outcome and date range.',
  'Access restricted to users holding the audit:read permission.',
  'Export of filtered results for compliance reviews.',
]

export function AuditLogsPage() {
  return (
    <div className="space-y-6">
      <Callout title="Planned feature — audit events are not loaded" tone="info">
        The backend records audit events and exposes them at <code className="font-mono">GET /audit-logs</code> for
        administrators with the <code className="font-mono">audit:read</code> permission. This page will display them
        once dashboard authentication is in place. No audit data is shown or fabricated here.
      </Callout>

      <Card title="Audit events" description="Security and data-access history" bodyClassName="p-0">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <caption className="sr-only">Audit events (not yet connected)</caption>
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50/70">
                {TABLE_HEADERS.map((header) => (
                  <th
                    key={header}
                    scope="col"
                    className="px-5 py-3 text-xs font-semibold tracking-wide text-slate-500 uppercase"
                  >
                    {header}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr>
                <td colSpan={TABLE_HEADERS.length}>
                  <EmptyState
                    icon="lock"
                    title="No audit events to display"
                    description="Sign-in and API integration are required before audit history can be retrieved."
                  />
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Event fields" description="Recorded by the backend for each audit event">
          <dl className="divide-y divide-slate-100">
            {EVENT_FIELDS.map((item) => (
              <div key={item.field} className="grid gap-1 py-2.5 first:pt-0 last:pb-0 sm:grid-cols-3 sm:gap-4">
                <dt className="font-mono text-sm text-navy-800">{item.field}</dt>
                <dd className="text-sm text-slate-500 sm:col-span-2">{item.description}</dd>
              </div>
            ))}
          </dl>
        </Card>

        <Card title="Planned capabilities">
          <ul className="space-y-3">
            {PLANNED.map((item) => (
              <li key={item} className="flex gap-3 text-sm text-navy-700">
                <Icon name="check" className="mt-0.5 size-4 shrink-0 text-brand-600" />
                {item}
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  )
}
