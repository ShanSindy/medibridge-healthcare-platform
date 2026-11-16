import { useState } from 'react'
import { CHART_COLORS } from '../../lib/chartColors'
import { formatHour, formatLongDate, formatMonthShort, formatMonthYear, formatWeekdayDay } from '../../lib/format'
import type { AppointmentDay, Hl7Hour, PatientDemographics, RegistrationMonth } from '../../types/domain'
import { BarChart, type BarSeries } from '../charts/BarChart'
import { BarList } from '../charts/BarList'
import { Card } from '../ui/Card'
import { SegmentedControl } from '../ui/SegmentedControl'

const APPOINTMENT_SERIES: BarSeries[] = [
  { key: 'completed', label: 'Completed', color: CHART_COLORS.teal },
  { key: 'scheduled', label: 'Scheduled', color: CHART_COLORS.blue },
  { key: 'cancelled', label: 'Cancelled', color: CHART_COLORS.amber },
]

export function AppointmentActivityCard({ days, className }: { days: AppointmentDay[]; className?: string }) {
  const total = days.reduce((sum, day) => sum + day.completed + day.scheduled + day.cancelled, 0)

  return (
    <Card
      title="Appointment activity"
      description={`${total} appointments from the past 7 days through the next 6`}
      className={className}
    >
      <BarChart
        label="Appointments per day by status, from 7 days ago to 6 days ahead"
        categoryLabel="Day"
        series={APPOINTMENT_SERIES}
        labelEvery={2}
        data={days.map((day) => ({
          id: day.date,
          axisLabel: day.isToday ? 'Today' : formatWeekdayDay(day.date),
          title: day.isToday ? `Today · ${formatLongDate(new Date(day.date))}` : formatLongDate(new Date(day.date)),
          highlighted: day.isToday,
          values: { completed: day.completed, scheduled: day.scheduled, cancelled: day.cancelled },
        }))}
      />
    </Card>
  )
}

const REGISTRATION_SERIES: BarSeries[] = [
  { key: 'manual', label: 'Manual entry', color: CHART_COLORS.teal },
  { key: 'hl7', label: 'HL7 ADT^A04', color: CHART_COLORS.blue },
]

export function RegistrationTrendCard({ months, beforeWindow }: { months: RegistrationMonth[]; beforeWindow: number }) {
  const inWindow = months.reduce((sum, month) => sum + month.manual + month.hl7, 0)
  const earlier = beforeWindow > 0 ? ` · ${beforeWindow} registered earlier` : ''

  return (
    <Card title="Patient registrations" description={`${inWindow} new records in the last 12 months${earlier}`}>
      <BarChart
        label="New patient records per month by registration source, last 12 months"
        categoryLabel="Month"
        series={REGISTRATION_SERIES}
        labelEvery={2}
        data={months.map((month) => ({
          id: month.monthStart,
          axisLabel: formatMonthShort(month.monthStart),
          title: formatMonthYear(month.monthStart),
          values: { manual: month.manual, hl7: month.hl7 },
        }))}
      />
    </Card>
  )
}

const HL7_SERIES: BarSeries[] = [
  { key: 'accepted', label: 'Accepted', color: CHART_COLORS.teal },
  { key: 'rejected', label: 'Rejected', color: CHART_COLORS.critical },
]

export function Hl7ActivityCard({ hours }: { hours: Hl7Hour[] }) {
  const accepted = hours.reduce((sum, hour) => sum + hour.accepted, 0)
  const rejected = hours.reduce((sum, hour) => sum + hour.rejected, 0)

  return (
    <Card title="HL7 message processing" description={`${accepted} accepted · ${rejected} rejected in the last 24 hours`}>
      <BarChart
        label="HL7 v2 messages per hour, accepted and rejected, last 24 hours"
        categoryLabel="Hour starting"
        series={HL7_SERIES}
        labelEvery={6}
        data={hours.map((hour) => ({
          id: hour.hourStart,
          axisLabel: formatHour(hour.hourStart),
          title: `${formatHour(hour.hourStart)} hour`,
          values: { accepted: hour.accepted, rejected: hour.rejected },
        }))}
      />
    </Card>
  )
}

type Breakdown = 'gender' | 'age'

const BREAKDOWN_OPTIONS: Array<{ value: Breakdown; label: string }> = [
  { value: 'gender', label: 'Gender' },
  { value: 'age', label: 'Age group' },
]

export function DemographicsCard({ demographics }: { demographics: PatientDemographics }) {
  const [breakdown, setBreakdown] = useState<Breakdown>('gender')
  const total = demographics.gender.reduce((sum, row) => sum + row.count, 0)

  return (
    <Card
      title="Patient demographics"
      description={`${total} registered patients`}
      actions={
        <SegmentedControl label="Breakdown" options={BREAKDOWN_OPTIONS} value={breakdown} onChange={setBreakdown} />
      }
    >
      <BarList
        rows={breakdown === 'gender' ? demographics.gender : demographics.ageGroups}
        color={CHART_COLORS.teal}
      />
    </Card>
  )
}
