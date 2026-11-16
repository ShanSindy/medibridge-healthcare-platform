/**
 * Reference "now" for the demo, captured once when the app loads.
 *
 * Using a single module-level timestamp keeps component renders pure
 * (no `Date.now()` during render) and keeps relative demo dates such as
 * "today's appointments" consistent across the whole session.
 */
export const DEMO_NOW = new Date()

/** Returns a Date `days` from today at the given local time. */
export function atDayOffset(days: number, hour: number, minute = 0): Date {
  const date = new Date(DEMO_NOW)
  date.setDate(date.getDate() + days)
  date.setHours(hour, minute, 0, 0)
  return date
}

export function minutesBeforeNow(minutes: number): string {
  return new Date(DEMO_NOW.getTime() - minutes * 60_000).toISOString()
}

export function isSameLocalDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  )
}
