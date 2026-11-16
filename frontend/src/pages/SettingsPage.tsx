import { useState, type FormEvent, type ReactNode } from 'react'
import { Button } from '../components/ui/Button'
import { CheckboxField, SelectField, TextField, type SelectOption } from '../components/ui/FormFields'
import { Callout } from '../components/ui/Misc'
import {
  clearSettings,
  DEFAULT_SETTINGS,
  loadSettings,
  saveSettings,
  type AppSettings,
} from '../services/settingsStore'

const TIME_ZONES: SelectOption<string>[] = [
  { value: 'America/Los_Angeles', label: 'Pacific Time (Los Angeles)' },
  { value: 'America/Denver', label: 'Mountain Time (Denver)' },
  { value: 'America/Chicago', label: 'Central Time (Chicago)' },
  { value: 'America/New_York', label: 'Eastern Time (New York)' },
  { value: 'UTC', label: 'Coordinated Universal Time (UTC)' },
]

const DATE_FORMATS: SelectOption<AppSettings['dateFormat']>[] = [
  { value: 'MMM d, yyyy', label: 'Nov 15, 2026' },
  { value: 'MM/dd/yyyy', label: '11/15/2026' },
  { value: 'yyyy-MM-dd', label: '2026-11-15 (ISO 8601)' },
]

const LATENCY_OPTIONS: SelectOption<string>[] = [
  { value: '0', label: 'None' },
  { value: '450', label: 'Typical (450 ms)' },
  { value: '1500', label: 'Slow network (1.5 s)' },
]

type SaveStatus = { kind: 'idle' } | { kind: 'saved' } | { kind: 'reset' } | { kind: 'error'; message: string }

export function SettingsPage() {
  const [settings, setSettings] = useState<AppSettings>(() => loadSettings())
  const [status, setStatus] = useState<SaveStatus>({ kind: 'idle' })
  const orgNameError = settings.organizationName.trim() === '' ? 'Organization name is required.' : undefined
  const [showErrors, setShowErrors] = useState(false)

  const update = (patch: Partial<AppSettings>) => {
    setSettings((current) => ({ ...current, ...patch }))
    setStatus({ kind: 'idle' })
  }
  const updateNotifications = (patch: Partial<AppSettings['notifications']>) =>
    update({ notifications: { ...settings.notifications, ...patch } })
  const updateDemo = (patch: Partial<AppSettings['demo']>) => update({ demo: { ...settings.demo, ...patch } })

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (orgNameError) {
      setShowErrors(true)
      setStatus({ kind: 'error', message: 'Please fix the highlighted field before saving.' })
      return
    }
    const cleaned = { ...settings, organizationName: settings.organizationName.trim() }
    if (saveSettings(cleaned)) {
      setSettings(cleaned)
      setShowErrors(false)
      setStatus({ kind: 'saved' })
    } else {
      setStatus({ kind: 'error', message: 'Browser storage is unavailable, so preferences could not be saved.' })
    }
  }

  const handleReset = () => {
    clearSettings()
    setSettings(structuredClone(DEFAULT_SETTINGS))
    setShowErrors(false)
    setStatus({ kind: 'reset' })
  }

  return (
    <form onSubmit={handleSubmit} noValidate className="space-y-6">
      <Callout title="Preferences are stored in this browser only" tone="info">
        Settings are saved to local browser storage for this demo. They are not sent to the MediBridge backend and
        do not affect other users.
      </Callout>

      <SettingsSection
        title="Organization"
        description="How this workspace is identified. Display preferences will apply once user profiles are connected."
      >
        <TextField
          label="Organization name"
          value={settings.organizationName}
          onChange={(organizationName) => update({ organizationName })}
          required
          maxLength={120}
          error={showErrors ? orgNameError : undefined}
        />
        <div className="grid gap-5 sm:grid-cols-2">
          <SelectField
            label="Time zone"
            value={settings.timeZone}
            options={TIME_ZONES}
            onChange={(timeZone) => update({ timeZone })}
          />
          <SelectField
            label="Date format"
            value={settings.dateFormat}
            options={DATE_FORMATS}
            onChange={(dateFormat) => update({ dateFormat })}
          />
        </div>
      </SettingsSection>

      <SettingsSection
        title="Notifications"
        description="Choose which alerts you would like to receive. Delivery will be enabled with backend integration."
      >
        <fieldset className="space-y-4">
          <legend className="sr-only">Notification preferences</legend>
          <CheckboxField
            label="Appointment reminders"
            description="Daily summary of upcoming visits for your providers."
            checked={settings.notifications.appointmentReminders}
            onChange={(appointmentReminders) => updateNotifications({ appointmentReminders })}
          />
          <CheckboxField
            label="Integration failure alerts"
            description="Notify when HL7 messages are rejected or FHIR requests fail."
            checked={settings.notifications.integrationFailures}
            onChange={(integrationFailures) => updateNotifications({ integrationFailures })}
          />
          <CheckboxField
            label="Weekly operations summary"
            description="Patient volume, scheduling and integration trends."
            checked={settings.notifications.weeklySummary}
            onChange={(weeklySummary) => updateNotifications({ weeklySummary })}
          />
        </fieldset>
      </SettingsSection>

      <SettingsSection
        title="Demo environment"
        description="Controls for reviewing the interface. These take effect the next time a page loads data."
      >
        <SelectField
          label="Simulated network latency"
          value={String(settings.demo.latencyMs)}
          options={LATENCY_OPTIONS}
          onChange={(value) => updateDemo({ latencyMs: Number(value) })}
          hint="Adds a delay to demo data requests so loading states are visible."
          className="sm:max-w-xs"
        />
        <CheckboxField
          label="Simulate API failure"
          description="Makes every demo data request fail so error and retry states can be reviewed."
          checked={settings.demo.simulateFailure}
          onChange={(simulateFailure) => updateDemo({ simulateFailure })}
        />
      </SettingsSection>

      <div className="flex flex-col-reverse gap-3 border-t border-slate-200 pt-5 sm:flex-row sm:items-center sm:justify-between">
        <p role="status" aria-live="polite" className="text-sm">
          {status.kind === 'saved' && (
            <span className="text-emerald-700">Preferences saved in this browser. Nothing was sent to a server.</span>
          )}
          {status.kind === 'reset' && <span className="text-navy-700">Preferences restored to defaults.</span>}
          {status.kind === 'error' && <span className="font-medium text-rose-700">{status.message}</span>}
        </p>
        <div className="flex gap-3">
          <Button onClick={handleReset}>Restore defaults</Button>
          <Button type="submit" variant="primary">
            Save preferences
          </Button>
        </div>
      </div>
    </form>
  )
}

function SettingsSection({ title, description, children }: { title: string; description: string; children: ReactNode }) {
  return (
    <section className="grid gap-x-8 gap-y-4 rounded-xl border border-slate-200/80 bg-white p-5 shadow-[0_1px_2px_rgba(16,24,40,0.04)] sm:p-6 lg:grid-cols-3">
      <div>
        <h2 className="text-[15px] font-semibold text-navy-900">{title}</h2>
        <p className="mt-1 text-sm text-slate-500">{description}</p>
      </div>
      <div className="space-y-5 lg:col-span-2">{children}</div>
    </section>
  )
}
