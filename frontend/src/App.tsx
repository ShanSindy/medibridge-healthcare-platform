import { useEffect, type ComponentType } from 'react'
import { AppShell } from './components/layout/AppShell'
import { useHashPath } from './hooks/useHashPath'
import { AppointmentsPage } from './pages/AppointmentsPage'
import { AuditLogsPage } from './pages/AuditLogsPage'
import { FhirExplorerPage } from './pages/FhirExplorerPage'
import { Hl7IntegrationPage } from './pages/Hl7IntegrationPage'
import { NotFoundPage } from './pages/NotFoundPage'
import { OverviewPage } from './pages/OverviewPage'
import { PatientsPage } from './pages/PatientsPage'
import { ProvidersPage } from './pages/ProvidersPage'
import { SettingsPage } from './pages/SettingsPage'
import { findRoute, type RouteId } from './routes'

const PAGES: Record<RouteId, ComponentType> = {
  overview: OverviewPage,
  patients: PatientsPage,
  providers: ProvidersPage,
  appointments: AppointmentsPage,
  fhir: FhirExplorerPage,
  hl7: Hl7IntegrationPage,
  audit: AuditLogsPage,
  settings: SettingsPage,
}

const APP_NAME = 'MediBridge Healthcare Platform'

function App() {
  const path = useHashPath()
  const route = findRoute(path)
  const Page = route ? PAGES[route.id] : NotFoundPage
  const title = route?.title ?? 'Page not found'
  const documentTitle = route?.id === 'overview' ? APP_NAME : `${route?.label ?? title} · ${APP_NAME}`

  useEffect(() => {
    document.title = documentTitle
  }, [documentTitle])

  return (
    <AppShell
      currentPath={path}
      section={route?.section ?? 'MediBridge'}
      pageLabel={route?.label ?? 'Not found'}
      title={title}
      description={route?.description}
    >
      {/* Keyed by path so each page mounts fresh and refetches its data. */}
      <Page key={path} />
    </AppShell>
  )
}

export default App
