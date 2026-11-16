import { useDeferredValue, useMemo, useState } from 'react'
import { PatientStatusBadge } from '../components/domain/StatusBadges'
import { AsyncContent } from '../components/ui/AsyncContent'
import { Badge } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { DataTable, type Column } from '../components/ui/DataTable'
import { SearchField, SelectField, type SelectOption } from '../components/ui/FormFields'
import { Avatar, DemoBadge, ResultCount } from '../components/ui/Misc'
import { EmptyState } from '../components/ui/States'
import { useAsync } from '../hooks/useAsync'
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
import { listPatients } from '../services/patients'
import { listProviders } from '../services/providers'
import type { Patient, PatientStatus, Provider } from '../types/domain'

type StatusFilter = 'all' | PatientStatus

const STATUS_OPTIONS: SelectOption<StatusFilter>[] = [
  { value: 'all', label: 'All statuses' },
  { value: 'active', label: 'Active' },
  { value: 'pending', label: 'Pending intake' },
  { value: 'inactive', label: 'Inactive' },
]

const fetchPatientDirectory = () => Promise.all([listPatients(), listProviders()])

export function PatientsPage() {
  const { state, reload } = useAsync(fetchPatientDirectory)
  return (
    <AsyncContent state={state} onRetry={reload} loadingLabel="Loading patients" errorTitle="Unable to load patients">
      {([patients, providers]) => <PatientRegistry patients={patients} providers={providers} />}
    </AsyncContent>
  )
}

function PatientRegistry({ patients, providers }: { patients: Patient[]; providers: Provider[] }) {
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState<StatusFilter>('all')
  const deferredQuery = useDeferredValue(query)

  const providerNames = useMemo(
    () => new Map(providers.map((provider) => [provider.id, providerName(provider)])),
    [providers],
  )

  const filtered = useMemo(
    () =>
      patients.filter(
        (patient) =>
          (status === 'all' || patient.status === status) &&
          matchesSearch(deferredQuery, patientName(patient), patient.mrn, patient.email, patient.phone),
      ),
    [patients, status, deferredQuery],
  )

  const hasFilters = query !== '' || status !== 'all'
  const clearFilters = () => {
    setQuery('')
    setStatus('all')
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
            <p className="text-xs text-slate-500">{patient.mrn}</p>
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
    {
      key: 'contact',
      header: 'Contact',
      cell: (patient) => (
        <div className="text-sm">
          <p className="tabular-nums">{patient.phone}</p>
          <p className="text-xs text-slate-500">{patient.email}</p>
        </div>
      ),
    },
    {
      key: 'provider',
      header: 'Primary provider',
      cell: (patient) =>
        patient.primaryProviderId ? (
          (providerNames.get(patient.primaryProviderId) ?? '—')
        ) : (
          <span className="text-slate-400">Unassigned</span>
        ),
    },
    {
      key: 'lastVisit',
      header: 'Last visit',
      cell: (patient) =>
        patient.lastVisit ? (
          <span className="tabular-nums">{formatDate(patient.lastVisit)}</span>
        ) : (
          <span className="text-slate-400">No visits</span>
        ),
    },
    {
      key: 'source',
      header: 'Source',
      cell: (patient) =>
        patient.registrationSource === 'hl7' ? <Badge tone="brand">HL7 ADT^A04</Badge> : <Badge>Manual entry</Badge>,
    },
    {
      key: 'status',
      header: 'Status',
      cell: (patient) => <PatientStatusBadge status={patient.status} />,
    },
  ]

  return (
    <Card
      title="Patient registry"
      description="Synthetic patients for demonstration — no real health information."
      actions={<DemoBadge />}
      bodyClassName="p-0"
    >
      <div className="flex flex-col gap-3 border-b border-slate-100 px-5 py-4 md:flex-row md:items-center">
        <SearchField
          label="Search patients"
          placeholder="Search by name, MRN, email or phone"
          value={query}
          onChange={setQuery}
          className="md:max-w-sm md:flex-1"
        />
        <SelectField
          label="Filter by status"
          hideLabel
          value={status}
          options={STATUS_OPTIONS}
          onChange={setStatus}
          className="md:w-48"
        />
        <div className="md:ml-auto">
          <ResultCount shown={filtered.length} total={patients.length} noun="patients" />
        </div>
      </div>

      <DataTable
        caption="Patients (synthetic demo data)"
        columns={columns}
        rows={filtered}
        getRowKey={(patient) => patient.id}
        minWidth="1040px"
        emptyState={
          <EmptyState
            icon="search"
            title={hasFilters ? 'No patients match your filters' : 'No patients registered'}
            description={
              hasFilters
                ? 'Try a different name or MRN, or clear the filters.'
                : 'Patients registered manually or via HL7 ADT^A04 will appear here.'
            }
            action={hasFilters ? <Button onClick={clearFilters}>Clear filters</Button> : undefined}
          />
        }
      />
    </Card>
  )
}
