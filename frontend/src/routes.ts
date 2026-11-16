import type { IconName } from './components/ui/Icon'

export type RouteId =
  | 'overview'
  | 'patients'
  | 'providers'
  | 'appointments'
  | 'fhir'
  | 'hl7'
  | 'audit'
  | 'settings'

export type NavSection = 'Workspace' | 'Clinical' | 'Interoperability' | 'Administration'

export interface AppRoute {
  id: RouteId
  path: string
  label: string
  title: string
  description: string
  icon: IconName
  section: NavSection
}

export const NAV_SECTIONS: NavSection[] = ['Workspace', 'Clinical', 'Interoperability', 'Administration']

export const ROUTES: AppRoute[] = [
  {
    id: 'overview',
    path: '/',
    label: 'Overview',
    title: 'Overview',
    description: 'Operational snapshot of patients, providers, scheduling and integrations.',
    icon: 'dashboard',
    section: 'Workspace',
  },
  {
    id: 'patients',
    path: '/patients',
    label: 'Patients',
    title: 'Patients',
    description: 'Search and review registered patients and their care status.',
    icon: 'users',
    section: 'Clinical',
  },
  {
    id: 'providers',
    path: '/providers',
    label: 'Providers',
    title: 'Provider directory',
    description: 'Clinicians, specialties and contact information across care sites.',
    icon: 'stethoscope',
    section: 'Clinical',
  },
  {
    id: 'appointments',
    path: '/appointments',
    label: 'Appointments',
    title: 'Appointments',
    description: 'Scheduled, completed and cancelled visits across all providers.',
    icon: 'calendar',
    section: 'Clinical',
  },
  {
    id: 'fhir',
    path: '/fhir',
    label: 'FHIR Explorer',
    title: 'FHIR Explorer',
    description: 'Browse MediBridge data as HL7® FHIR® R4 resources.',
    icon: 'braces',
    section: 'Interoperability',
  },
  {
    id: 'hl7',
    path: '/hl7',
    label: 'HL7 Integration',
    title: 'HL7 v2 integration',
    description: 'Inbound HL7 v2 ADT message processing and acknowledgements.',
    icon: 'arrows',
    section: 'Interoperability',
  },
  {
    id: 'audit',
    path: '/audit-logs',
    label: 'Audit Logs',
    title: 'Audit logs',
    description: 'Security and data-access events for compliance review.',
    icon: 'shield',
    section: 'Administration',
  },
  {
    id: 'settings',
    path: '/settings',
    label: 'Settings',
    title: 'Settings',
    description: 'Workspace preferences and demo environment controls.',
    icon: 'sliders',
    section: 'Administration',
  },
]

export function findRoute(path: string): AppRoute | undefined {
  return ROUTES.find((route) => route.path === path)
}

export function routeHref(path: string): string {
  return `#${path}`
}
