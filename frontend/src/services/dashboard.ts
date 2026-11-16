import { demoActivity, demoIntegrationMetrics } from '../data/demo/activity'
import { demoAppointments } from '../data/demo/appointments'
import { demoPatients } from '../data/demo/patients'
import { demoProviders } from '../data/demo/providers'
import { atDayOffset, DEMO_NOW, isSameLocalDay } from '../lib/clock'
import type { AppointmentStatus, DashboardOverview } from '../types/domain'
import { toAppointmentDetails } from './appointments'
import { demoRequest } from './demoRequest'

/**
 * Aggregated data for the overview dashboard.
 *
 * Future integration: a dedicated summary endpoint (not yet in the backend)
 * would avoid computing aggregates in the browser.
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
      todaysAppointments,
      recentActivity: [...demoActivity].sort((a, b) => b.occurredAt.localeCompare(a.occurredAt)),
    }
  })
}
