import { useDeferredValue, useMemo, useState } from 'react'
import { SignInPanel } from '../components/auth/SignInPanel'
import { PatientStatusBadge } from '../components/domain/StatusBadges'
import { AsyncContent } from '../components/ui/AsyncContent'
import { Badge } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { DataTable, type Column } from '../components/ui/DataTable'
import { SearchField, SelectField, type SelectOption } from '../components/ui/FormFields'
import { Avatar, Callout, DemoBadge, ResultCount } from '../components/ui/Misc'
import { EmptyState, ErrorState, LoadingState } from '../components/ui/States'
import { DATA_SOURCE } from '../config/api'
import { useAsync } from '../hooks/useAsync'
import { useAuthSession } from '../hooks/useAuthSession'
import {
  ageInYears,
  formatDate,
  formatDateOnly,
  formatGender,
  initials,
  matchesSearch,
  patientName,
  providerName,
} from '../lib/format'
import { signOut, type AuthSession, type AuthUser } from '../services/auth'
import { isApiError } from '../services/http'
import { listPatients } from '../services/patients'
import { listProviders } from '../services/providers'
import type { Gender, Patient, PatientStatus, Provider, RecordSource } from '../types/domain'

type StatusFilter = 'all' | PatientStatus
type GenderFilter = 'all' | Gender

const STATUS_OPTIONS: SelectOption<StatusFilter>[] = [
  { value: 'all', label: 'All statuses' },
  { value: 'active', label: 'Active' },
  { value: 'pending', label: 'Pending intake' },
  { value: 'inactive', label: 'Inactive' },
]

const GENDER_OPTIONS: SelectOption<GenderFilter>[] = [
  { value: 'all', label: 'All genders' },
  { value: 'female', label: 'Female' },
  { value: 'male', label: 'Male' },
  { value: 'other', label: 'Other' },
  { value: 'unknown', label: 'Unknown' },
]

export function PatientsPage() {
  return DATA_SOURCE === 'api' ? <ApiPatientsView /> : <DemoPatientsView />
}

/* ------------------------------------------------------------------ */
/* Demo mode                                                           */
/* ------------------------------------------------------------------ */

const fetchDemoDirectory = () => Promise.all([listPatients({ source: 'demo' }), listProviders()])

function DemoPatientsView() {
  const { state, reload } = useAsync(fetchDemoDirectory)
  return (
    <AsyncContent state={state} onRetry={reload} loadingLabel="Loading patients" errorTitle="Unable to load patients">
      {([patients, providers]) => <PatientRegistry patients={patients} providers={providers} source="demo" />}
    </AsyncContent>
  )
}

/* ------------------------------------------------------------------ */
/* API mode                                                            */
/* ------------------------------------------------------------------ */

function ApiPatientsView() {
  const { session, endedReason } = useAuthSession()
  if (!session) return <SignInPanel endedReason={endedReason} resourceLabel="patient records" />
  // Keyed by token so a new sign-in always refetches.
  return <ApiPatientList key={session.accessToken} session={session} />
}

const fetchApiPatients = () => listPatients({ source: 'api' })

function ApiPatientList({ session }: { session: AuthSession }) {
  const { state, reload } = useAsync(fetchApiPatients)

  if (state.status === 'loading') return <LoadingState label="Loading patients from the MediBridge API" />

  if (state.status === 'error') {
    const { error } = state
    if (isApiError(error, 'forbidden')) return <ForbiddenState user={session.user} />
    // A 401 ends the session, which swaps this view for the sign-in form.
    return <ErrorState title="Unable to load patients" message={error.message} onRetry={reload} />
  }

  return (
    <div className="space-y-4">
      <Callout title="Live data from the MediBridge API">
        The patient endpoint currently provides name, date of birth and gender. Contact details, care status, primary
        provider, visit history and registration source are not returned by the backend, so they are not shown.
      </Callout>
      <PatientRegistry patients={state.data} providers={[]} source="api" />
    </div>
  )
}

function ForbiddenState({ user }: { user: AuthUser | null }) {
  return (
    <Card>
      <EmptyState
        icon="lock"
        title="You don’t have access to patient records"
        description={
          <>
            {user && (
              <>
                Signed in as <strong className="font-medium text-navy-800">{user.username}</strong> ({user.role}).{' '}
              </>
            )}
            Viewing patients requires the admin, clinician or scheduler role. Ask an administrator for access, or sign
            in with a different account.
          </>
        }
        action={<Button onClick={signOut}>Sign out</Button>}
      />
    </Card>
  )
}

/* ------------------------------------------------------------------ */
/* Shared registry table                                               */
/* ------------------------------------------------------------------ */

function NotAvailable() {
  return (
    <span className="text-slate-400">
      <span aria-hidden="true">—</span>
      <span className="sr-only">Not available</span>
    </span>
  )
}

function patientIdentifier(patient: Patient): string {
  return patient.mrn ?? `ID ${patient.id}`
}

interface PatientRegistryProps {
  patients: Patient[]
  providers: Provider[]
  source: RecordSource
}

function PatientRegistry({ patients, providers, source }: PatientRegistryProps) {
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState<StatusFilter>('all')
  const [gender, setGender] = useState<GenderFilter>('all')
  const deferredQuery = useDeferredValue(query)

  // Only show columns and filters for fields the data source actually provides.
  const available = useMemo(
    () => ({
      status: patients.some((p) => p.status !== null),
      contact: patients.some((p) => p.phone !== null || p.email !== null),
      provider: patients.some((p) => p.primaryProviderId !== null),
      lastVisit: patients.some((p) => p.lastVisit !== null),
      registration: patients.some((p) => p.registrationSource !== null),
    }),
    [patients],
  )

  const providerNames = useMemo(
    () => new Map(providers.map((provider) => [provider.id, providerName(provider)])),
    [providers],
  )

  const filtered = useMemo(
    () =>
      patients.filter(
        (patient) =>
          (status === 'all' || patient.status === status) &&
          (gender === 'all' || patient.gender === gender) &&
          matchesSearch(
            deferredQuery,
            patientName(patient),
            `${patient.lastName}, ${patient.firstName}`,
            patientIdentifier(patient),
            patient.email,
            patient.phone,
          ),
      ),
    [patients, status, gender, deferredQuery],
  )

  const hasFilters = query !== '' || status !== 'all' || gender !== 'all'
  const clearFilters = () => {
    setQuery('')
    setStatus('all')
    setGender('all')
  }

  const columns: Column<Patient>[] = [
    {
      key: 'patient',
      header: 'Patient',
      cell: (patient) => (
        <div className="flex items-center gap-3">
          <Avatar initials={initials(patient.firstName, patient.lastName)} />
          <div className="min-w-0">
            <p className="font-medium text-navy-900">
              {patient.lastName}, {patient.firstName}
            </p>
            <p className="text-xs text-slate-500">{patientIdentifier(patient)}</p>
          </div>
        </div>
      ),
    },
    {
      key: 'dob',
      header: 'Date of birth',
      cell: (patient) => (
        <div>
          <p className="tabular-nums">{formatDateOnly(patient.dateOfBirth)}</p>
          <p className="text-xs text-slate-500">
            {ageInYears(patient.dateOfBirth)} yrs · {formatGender(patient.gender)}
          </p>
        </div>
      ),
    },
  ]

  if (available.contact) {
    columns.push({
      key: 'contact',
      header: 'Contact',
      cell: (patient) =>
        patient.phone || patient.email ? (
          <div className="text-sm">
            {patient.phone && <p className="tabular-nums">{patient.phone}</p>}
            {patient.email && <p className="text-xs text-slate-500">{patient.email}</p>}
          </div>
        ) : (
          <NotAvailable />
        ),
    })
  }

  if (available.provider) {
    columns.push({
      key: 'provider',
      header: 'Primary provider',
      cell: (patient) =>
        patient.primaryProviderId !== null ? (
          (providerNames.get(patient.primaryProviderId) ?? <NotAvailable />)
        ) : patient.recordSource === 'demo' ? (
          <span className="text-slate-400">Unassigned</span>
        ) : (
          <NotAvailable />
        ),
    })
  }

  if (available.lastVisit) {
    columns.push({
      key: 'lastVisit',
      header: 'Last visit',
      cell: (patient) =>
        patient.lastVisit ? (
          <span className="tabular-nums">{formatDate(patient.lastVisit)}</span>
        ) : patient.recordSource === 'demo' ? (
          <span className="text-slate-400">No visits</span>
        ) : (
          <NotAvailable />
        ),
    })
  }

  if (available.registration) {
    columns.push({
      key: 'source',
      header: 'Source',
      cell: (patient) =>
        patient.registrationSource === 'hl7' ? (
          <Badge tone="brand">HL7 ADT^A04</Badge>
        ) : patient.registrationSource === 'manual' ? (
          <Badge>Manual entry</Badge>
        ) : (
          <NotAvailable />
        ),
    })
  }

  if (available.status) {
    columns.push({
      key: 'status',
      header: 'Status',
      cell: (patient) => (patient.status ? <PatientStatusBadge status={patient.status} /> : <NotAvailable />),
    })
  }

  const isDemo = source === 'demo'

  return (
    <Card
      title="Patient registry"
      description={
        isDemo ? (
          'Synthetic patients for demonstration — no real health information.'
        ) : (
          <>
            Records returned by the MediBridge API (<code className="font-mono text-xs">GET /patients</code>).
          </>
        )
      }
      actions={
        isDemo ? (
          <DemoBadge />
        ) : (
          <Badge tone="brand" dot>
            Live API data
          </Badge>
        )
      }
      bodyClassName="p-0"
    >
      <div className="flex flex-col gap-3 border-b border-slate-100 px-5 py-4 md:flex-row md:items-center">
        <SearchField
          label="Search patients"
          placeholder={isDemo ? 'Search by name, MRN, email or phone' : 'Search by name or patient ID'}
          value={query}
          onChange={setQuery}
          className="md:max-w-sm md:flex-1"
        />
        <div className="grid grid-cols-2 gap-3 md:flex">
          {available.status && (
            <SelectField
              label="Filter by status"
              hideLabel
              value={status}
              options={STATUS_OPTIONS}
              onChange={setStatus}
              className="md:w-44"
            />
          )}
          <SelectField
            label="Filter by gender"
            hideLabel
            value={gender}
            options={GENDER_OPTIONS}
            onChange={setGender}
            className="md:w-40"
          />
        </div>
        <div className="md:ml-auto">
          <ResultCount shown={filtered.length} total={patients.length} noun="patients" />
        </div>
      </div>

      <DataTable
        caption={isDemo ? 'Patients (synthetic demo data)' : 'Patients (MediBridge API)'}
        columns={columns}
        rows={filtered}
        getRowKey={(patient) => `${patient.recordSource}-${patient.id}`}
        minWidth={columns.length > 3 ? '1040px' : '560px'}
        emptyState={
          <EmptyState
            icon="search"
            title={hasFilters ? 'No patients match your filters' : 'No patients found'}
            description={
              hasFilters
                ? 'Try a different name or identifier, or clear the filters.'
                : isDemo
                  ? 'Patients registered manually or via HL7 ADT^A04 will appear here.'
                  : 'The MediBridge API returned no patient records.'
            }
            action={hasFilters ? <Button onClick={clearFilters}>Clear filters</Button> : undefined}
          />
        }
      />
    </Card>
  )
}
