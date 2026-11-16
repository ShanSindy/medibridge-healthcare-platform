import { describe, expect, it } from 'vitest'
import { appointmentsByDay, hl7ByHour, patientDemographics, registrationsByMonth } from './dashboardTrends'

/** Local-time reference: Sunday 15 November 2026, 21:10. */
const NOW = new Date(2026, 10, 15, 21, 10)

const local = (year: number, month: number, day: number, hour = 9) => new Date(year, month - 1, day, hour).toISOString()

describe('registrationsByMonth', () => {
  it('returns one bucket per month, oldest first, ending with the current month', () => {
    const { months } = registrationsByMonth([], NOW)
    expect(months).toHaveLength(12)
    expect(new Date(months[0].monthStart).getMonth()).toBe(11) // December 2025
    expect(new Date(months[11].monthStart).getMonth()).toBe(10) // November 2026
    expect(months.every((month) => month.manual === 0 && month.hl7 === 0)).toBe(true)
  })

  it('counts records by month and registration source', () => {
    const { months, beforeWindow } = registrationsByMonth(
      [
        { registeredAt: local(2026, 11, 1), registrationSource: 'manual' },
        { registeredAt: local(2026, 11, 15), registrationSource: 'hl7' },
        { registeredAt: local(2026, 10, 31), registrationSource: 'hl7' },
        { registeredAt: local(2025, 12, 1), registrationSource: 'manual' },
      ],
      NOW,
    )
    expect(months[11]).toMatchObject({ manual: 1, hl7: 1 })
    expect(months[10]).toMatchObject({ manual: 0, hl7: 1 })
    expect(months[0]).toMatchObject({ manual: 1, hl7: 0 })
    expect(beforeWindow).toBe(0)
  })

  it('reports records older than the window separately instead of dropping them silently', () => {
    const { months, beforeWindow } = registrationsByMonth(
      [{ registeredAt: local(2025, 11, 30), registrationSource: 'manual' }],
      NOW,
    )
    expect(beforeWindow).toBe(1)
    expect(months.reduce((sum, month) => sum + month.manual + month.hl7, 0)).toBe(0)
  })

  it('skips records with no registration date or source, and future dates', () => {
    const { months, beforeWindow } = registrationsByMonth(
      [
        { registeredAt: null, registrationSource: null },
        { registeredAt: local(2026, 11, 2), registrationSource: null },
        { registeredAt: local(2026, 12, 1), registrationSource: 'manual' },
      ],
      NOW,
    )
    expect(beforeWindow).toBe(0)
    expect(months.reduce((sum, month) => sum + month.manual + month.hl7, 0)).toBe(0)
  })
})

describe('appointmentsByDay', () => {
  it('covers 7 days back through 6 days ahead and marks today', () => {
    const days = appointmentsByDay([], NOW)
    expect(days).toHaveLength(14)
    expect(days.filter((day) => day.isToday)).toHaveLength(1)
    expect(days[7].isToday).toBe(true)
    expect(new Date(days[0].date).getDate()).toBe(8)
    expect(new Date(days[13].date).getDate()).toBe(21)
  })

  it('counts appointments on their local day by status and ignores those outside the window', () => {
    const days = appointmentsByDay(
      [
        { startsAt: local(2026, 11, 15, 8), status: 'completed' },
        { startsAt: local(2026, 11, 15, 23), status: 'cancelled' },
        { startsAt: local(2026, 11, 16, 0), status: 'scheduled' },
        { startsAt: local(2026, 11, 7, 12), status: 'completed' },
        { startsAt: local(2026, 11, 22, 12), status: 'scheduled' },
      ],
      NOW,
    )
    expect(days[7]).toMatchObject({ completed: 1, cancelled: 1, scheduled: 0 })
    expect(days[8]).toMatchObject({ completed: 0, cancelled: 0, scheduled: 1 })
    const total = days.reduce((sum, day) => sum + day.completed + day.scheduled + day.cancelled, 0)
    expect(total).toBe(3)
  })
})

describe('hl7ByHour', () => {
  const volume = Array.from({ length: 24 }, (_, hour) => hour + 1)

  it('lays out the 24 clock hours ending with the current hour', () => {
    const hours = hl7ByHour(volume, [], NOW)
    expect(hours).toHaveLength(24)
    expect(new Date(hours[23].hourStart).getHours()).toBe(21)
    expect(new Date(hours[0].hourStart).getHours()).toBe(22)
    expect(hours[23].accepted).toBe(22)
  })

  it('keeps the total volume and moves rejected messages out of accepted', () => {
    const hours = hl7ByHour(volume, [0, 0, 5], NOW)
    const accepted = hours.reduce((sum, hour) => sum + hour.accepted, 0)
    const rejected = hours.reduce((sum, hour) => sum + hour.rejected, 0)
    expect(rejected).toBe(3)
    expect(accepted + rejected).toBe(volume.reduce((sum, value) => sum + value, 0))
    expect(hours[23]).toMatchObject({ accepted: 20, rejected: 2 })
  })

  it('never reports more rejected messages than an hour received', () => {
    const hours = hl7ByHour(new Array(24).fill(1), [0, 0, 0], NOW)
    expect(hours[23]).toMatchObject({ accepted: 0, rejected: 1 })
  })
})

describe('patientDemographics', () => {
  it('counts every gender and age group, including empty ones', () => {
    const demographics = patientDemographics(
      [
        { gender: 'female', dateOfBirth: '2010-01-01' },
        { gender: 'female', dateOfBirth: '1990-06-15' },
        { gender: 'male', dateOfBirth: '1950-03-03' },
        { gender: 'unknown', dateOfBirth: '1970-12-31' },
      ],
      NOW,
    )
    expect(demographics.gender).toEqual([
      { label: 'Female', count: 2 },
      { label: 'Male', count: 1 },
      { label: 'Other', count: 0 },
      { label: 'Unknown', count: 1 },
    ])
    expect(demographics.ageGroups).toEqual([
      { label: '0–17', count: 1 },
      { label: '18–39', count: 1 },
      { label: '40–64', count: 1 },
      { label: '65+', count: 1 },
    ])
  })

  it('puts a patient in the older group only once their birthday has passed', () => {
    const groups = (dateOfBirth: string) =>
      patientDemographics([{ gender: 'other', dateOfBirth }], NOW).ageGroups.map((group) => group.count)
    expect(groups('2008-11-15')).toEqual([0, 1, 0, 0]) // turns 18 today
    expect(groups('2008-11-16')).toEqual([1, 0, 0, 0]) // turns 18 tomorrow
  })

  it('returns zero counts for an empty list', () => {
    const demographics = patientDemographics([], NOW)
    expect(demographics.gender.every((row) => row.count === 0)).toBe(true)
    expect(demographics.ageGroups.every((row) => row.count === 0)).toBe(true)
  })
})
