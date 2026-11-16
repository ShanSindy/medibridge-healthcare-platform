import { Badge } from '../components/ui/Badge'
import { Card } from '../components/ui/Card'
import { DataTable, type Column } from '../components/ui/DataTable'
import { Icon } from '../components/ui/Icon'
import { Callout, CodeBlock } from '../components/ui/Misc'
import { EmptyState } from '../components/ui/States'

interface FhirResource {
  resource: string
  interactions: string
  endpoints: string[]
  backend: 'available' | 'future'
}

const RESOURCES: FhirResource[] = [
  {
    resource: 'CapabilityStatement',
    interactions: 'capabilities',
    endpoints: ['GET /fhir/metadata'],
    backend: 'available',
  },
  {
    resource: 'Patient',
    interactions: 'read, search',
    endpoints: ['GET /fhir/Patient/{id}', 'GET /fhir/Patient'],
    backend: 'available',
  },
  {
    resource: 'Practitioner',
    interactions: 'read, search',
    endpoints: ['GET /fhir/Practitioner/{id}', 'GET /fhir/Practitioner'],
    backend: 'available',
  },
  {
    resource: 'PractitionerRole',
    interactions: 'read, search by specialty',
    endpoints: ['GET /fhir/PractitionerRole/{id}', 'GET /fhir/PractitionerRole?specialty=…'],
    backend: 'available',
  },
  {
    resource: 'Appointment',
    interactions: '—',
    endpoints: ['Not yet exposed as FHIR'],
    backend: 'future',
  },
]

const COLUMNS: Column<FhirResource>[] = [
  {
    key: 'resource',
    header: 'Resource',
    cell: (row) => <span className="font-medium text-navy-900">{row.resource}</span>,
  },
  { key: 'interactions', header: 'Interactions', cell: (row) => row.interactions },
  {
    key: 'endpoints',
    header: 'Backend endpoint',
    cell: (row) => (
      <ul className="space-y-1">
        {row.endpoints.map((endpoint) => (
          <li key={endpoint}>
            <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-navy-700">{endpoint}</code>
          </li>
        ))}
      </ul>
    ),
  },
  {
    key: 'status',
    header: 'Status',
    cell: (row) =>
      row.backend === 'available' ? (
        <Badge tone="brand" dot>
          Backend ready · UI planned
        </Badge>
      ) : (
        <Badge tone="neutral" dot>
          Future
        </Badge>
      ),
  },
]

const SAMPLE_REQUEST = `GET /fhir/Patient?family=Thompson
Accept: application/fhir+json
Authorization: Bearer <token>`

const SAMPLE_RESPONSE = `{
  "resourceType": "Bundle",
  "type": "searchset",
  "total": 1,
  "entry": [
    {
      "resource": {
        "resourceType": "Patient",
        "id": "1",
        "name": [{ "family": "Thompson", "given": ["Avery"] }],
        "gender": "female",
        "birthDate": "1984-03-12"
      }
    }
  ]
}`

const PLANNED_FEATURES = [
  'Resource picker with guided search parameters (name, birthdate, specialty).',
  'Formatted JSON viewer with copy-to-clipboard and Bundle pagination.',
  'OperationOutcome errors shown as readable validation messages.',
  'Requests authenticated with the signed-in user’s backend token.',
]

export function FhirExplorerPage() {
  return (
    <div className="space-y-6">
      <Callout title="Planned feature — not connected" tone="info">
        The MediBridge backend already serves several HL7® FHIR® R4 endpoints. This explorer will let administrators
        query them from the dashboard once authentication and API integration are added. No requests are sent from
        this page today.
      </Callout>

      <Card
        title="FHIR R4 resources"
        description="Resources implemented in the FastAPI backend and their dashboard status"
        bodyClassName="p-0"
      >
        <DataTable
          caption="FHIR R4 resources supported by the backend"
          columns={COLUMNS}
          rows={RESOURCES}
          getRowKey={(row) => row.resource}
          minWidth="760px"
          emptyState={<EmptyState title="No resources" />}
        />
      </Card>

      <div className="grid gap-6 lg:grid-cols-5">
        <Card
          title="Example query"
          description="Illustrative only, using synthetic data. Not a live response."
          className="lg:col-span-3"
        >
          <div className="space-y-4">
            <CodeBlock label="Request" code={SAMPLE_REQUEST} />
            <CodeBlock label="Response (synthetic example)" code={SAMPLE_RESPONSE} />
          </div>
        </Card>

        <Card title="Planned explorer capabilities" className="lg:col-span-2">
          <ul className="space-y-3">
            {PLANNED_FEATURES.map((feature) => (
              <li key={feature} className="flex gap-3 text-sm text-navy-700">
                <Icon name="check" className="mt-0.5 size-4 shrink-0 text-brand-600" />
                {feature}
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  )
}
