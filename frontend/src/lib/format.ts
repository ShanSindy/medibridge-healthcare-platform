import type { Gender, Patient, Provider } from '../types/domain'
import { DEMO_NOW } from './clock'

const LOCALE = 'en-US'

const dateFormatter = new Intl.DateTimeFormat(LOCALE, {
  month: 'short',
  day: 'numeric',
  year: 'numeric',
})
const timeFormatter = new Intl.DateTimeFormat(LOCALE, { hour: 'numeric', minute: '2-digit' })
const weekdayFormatter = new Intl.DateTimeFormat(LOCALE, {
  weekday: 'short',
  month: 'short',
  day: 'numeric',
})
const longDateFormatter = new Intl.DateTimeFormat(LOCALE, {
  weekday: 'long',
  month: 'long',
  day: 'numeric',
  year: 'numeric',
})
const relativeFormatter = new Intl.RelativeTimeFormat(LOCALE, { numeric: 'auto' })
const numberFormatter = new Intl.NumberFormat(LOCALE)

/** Parses a `YYYY-MM-DD` value as a local calendar date (avoids UTC off-by-one). */
export function parseDateOnly(value: string): Date {
  const [year, month, day] = value.split('-').map(Number)
  return new Date(year, month - 1, day)
}

export function formatDateOnly(value: string): string {
  return dateFormatter.format(parseDateOnly(value))
}

export function formatDate(iso: string): string {
  return dateFormatter.format(new Date(iso))
}

export function formatTime(iso: string): string {
  return timeFormatter.format(new Date(iso))
}

export function formatTimeRange(startIso: string, endIso: string): string {
  return `${formatTime(startIso)} – ${formatTime(endIso)}`
}

export function formatWeekdayDate(iso: string): string {
  return weekdayFormatter.format(new Date(iso))
}

export function formatLongDate(date: Date): string {
  return longDateFormatter.format(date)
}

export function formatNumber(value: number): string {
  return numberFormatter.format(value)
}

export function durationMinutes(startIso: string, endIso: string): number {
  return Math.round((new Date(endIso).getTime() - new Date(startIso).getTime()) / 60_000)
}

export function ageInYears(dateOfBirth: string, reference: Date = DEMO_NOW): number {
  const dob = parseDateOnly(dateOfBirth)
  let age = reference.getFullYear() - dob.getFullYear()
  const beforeBirthday =
    reference.getMonth() < dob.getMonth() ||
    (reference.getMonth() === dob.getMonth() && reference.getDate() < dob.getDate())
  if (beforeBirthday) age -= 1
  return age
}

export function formatRelative(iso: string, reference: Date = DEMO_NOW): string {
  const diffSeconds = Math.round((new Date(iso).getTime() - reference.getTime()) / 1000)
  const abs = Math.abs(diffSeconds)
  if (abs < 60) return relativeFormatter.format(diffSeconds, 'second')
  if (abs < 3600) return relativeFormatter.format(Math.round(diffSeconds / 60), 'minute')
  if (abs < 86_400) return relativeFormatter.format(Math.round(diffSeconds / 3600), 'hour')
  return relativeFormatter.format(Math.round(diffSeconds / 86_400), 'day')
}

const GENDER_LABELS: Record<Gender, string> = {
  male: 'Male',
  female: 'Female',
  other: 'Other',
  unknown: 'Unknown',
}

export function formatGender(gender: Gender): string {
  return GENDER_LABELS[gender]
}

export function initials(firstName: string, lastName: string): string {
  return `${firstName.charAt(0)}${lastName.charAt(0)}`.toUpperCase()
}

export function patientName(patient: Pick<Patient, 'firstName' | 'lastName'>): string {
  return `${patient.firstName} ${patient.lastName}`
}

export function providerName(provider: Pick<Provider, 'prefix' | 'firstName' | 'lastName'>): string {
  const name = `${provider.firstName} ${provider.lastName}`
  return provider.prefix ? `${provider.prefix} ${name}` : name
}

/** Normalises text for case- and accent-insensitive searching. */
export function normalizeSearch(value: string): string {
  return value
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .trim()
}

export function matchesSearch(query: string, ...fields: Array<string | null | undefined>): boolean {
  const needle = normalizeSearch(query)
  if (!needle) return true
  return fields.some((field) => field != null && normalizeSearch(field).includes(needle))
}
