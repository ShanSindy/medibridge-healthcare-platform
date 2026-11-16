import {
  demoActivity,
  demoHl7RejectedHoursAgo,
  demoHl7VolumeByHourOfDay,
  demoIntegrationMetrics,
} from '../data/demo/activity'
import { demoAppointments } from '../data/demo/appointments'
import { demoPatients } from '../data/demo/patients'
import { demoProviders } from '../data/demo/providers'
import { atDayOffset, DEMO_NOW, isSameLocalDay } from '../lib/clock'
import type { AppointmentStatus, DashboardOverview } from '../types/domain'
import { toAppointmentDetails } from './appointments'
import { appointmentsByDay, hl7ByHour, patientDemographics, registrationsByMonth } from './dashboardTrends'
import { demoRequest } from './demoRequest'

/**
 * Aggregated data for the dashboard.
 *
 * Demo only: the backend has no summary, trend or activity-feed endpoint,
 * so every figure here is derived from the synthetic in-browser records.
 */
export function getDashboardOverview(): Promise<DashboardOverview> {
  return demoRequest(() => {
    const weekEnd = atDayOffset(7, 23, 59)
    const byStatus: Record<AppointmentStatus, number> = { scheduled: 0, completed: 0, cancelled: 0 }
    let upcomingWeek = 0

    for (const appointment of demoAppointments) {
      byStatus[appointment.status] += 1
      const start = new Date(appointment.startsAt)
      if (appointment.status === 'scheduled' && start >= DEMO_NOW && start <= weekEnd) {
        upcomingWeek += 1
      }
    }

    const todaysAppointments = toAppointmentDetails(
      demoAppointments.filter((a) => isSameLocalDay(new Date(a.startsAt), DEMO_NOW)),
      demoPatients,
      demoProviders,
    )

    const registrations = registrationsByMonth(demoPatients, DEMO_NOW)

    return {
      summary: {
        patients: {
          total: demoPatients.length,
          active: demoPatients.filter((p) => p.status === 'active').length,
          pending: demoPatients.filter((p) => p.status === 'pending').length,
        },
        providers: {
          total: demoProviders.length,
          acceptingNewPatients: demoProviders.filter((p) => p.acceptingNewPatients).length,
        },
        appointments: {
          today: todaysAppointments.length,
          upcomingWeek,
          total: demoAppointments.length,
          byStatus,
        },
        integration: demoIntegrationMetrics,
      },
      trends: {
        registrationsByMonth: registrations.months,
        registrationsBeforeWindow: registrations.beforeWindow,
        appointmentsByDay: appointmentsByDay(demoAppointments, DEMO_NOW),
        hl7ByHour: hl7ByHour(demoHl7VolumeByHourOfDay, demoHl7RejectedHoursAgo, DEMO_NOW),
      },
      demographics: patientDemographics(demoPatients, DEMO_NOW),
      todaysAppointments,
      recentActivity: [...demoActivity].sort((a, b) => b.occurredAt.localeCompare(a.occurredAt)),
    }
  })
}
