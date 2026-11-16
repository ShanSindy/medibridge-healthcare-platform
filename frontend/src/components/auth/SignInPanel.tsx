import { useState, type FormEvent } from 'react'
import { API_BASE_URL } from '../../config/api'
import { signIn, type SessionEndReason } from '../../services/auth'
import { isApiError } from '../../services/http'
import { Button } from '../ui/Button'
import { Card } from '../ui/Card'
import { TextField } from '../ui/FormFields'
import { Icon } from '../ui/Icon'
import { Callout } from '../ui/Misc'

interface SignInPanelProps {
  /** Why the previous session ended, if it did. */
  endedReason: SessionEndReason
  /** What the user is signing in to see, e.g. "patient records". */
  resourceLabel: string
  /** Who may view the resource. Defaults to the roles allowed to read patients. */
  accessNote?: string
}

/**
 * Sign-in form for the MediBridge API (`POST /auth/login`).
 * On success the shared auth store updates and the caller re-renders.
 */
export function SignInPanel({
  endedReason,
  resourceLabel,
  accessNote = 'Access requires the admin, clinician or scheduler role.',
}: SignInPanelProps) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!username.trim() || !password) {
      setError('Enter your username and password.')
      return
    }
    setPending(true)
    setError(null)
    try {
      await signIn(username.trim(), password)
      // Component unmounts once the session is set.
    } catch (caught) {
      setPassword('')
      setError(isApiError(caught) ? caught.message : 'Sign-in failed. Please try again.')
      setPending(false)
    }
  }

  return (
    <div className="mx-auto max-w-md space-y-4">
      {endedReason === 'expired' && (
        <Callout title="Your session has ended" tone="warning">
          Your access token expired or was rejected by the server. Sign in again to continue.
        </Callout>
      )}
      <Card>
        <div className="mb-5 flex items-start gap-3">
          <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-brand-700">
            <Icon name="lock" className="size-5" />
          </span>
          <div>
            <h2 className="text-[15px] font-semibold text-navy-900">Sign in to view {resourceLabel}</h2>
            <p className="mt-0.5 text-sm text-slate-500">
              Use your MediBridge account. {accessNote}
            </p>
          </div>
        </div>

        <form onSubmit={handleSubmit} noValidate className="space-y-4" aria-busy={pending}>
          <TextField
            label="Username"
            value={username}
            onChange={setUsername}
            autoComplete="username"
            required
            disabled={pending}
          />
          <TextField
            label="Password"
            type="password"
            value={password}
            onChange={setPassword}
            autoComplete="current-password"
            required
            disabled={pending}
          />
          {error && (
            <p role="alert" className="text-sm font-medium text-rose-700">
              {error}
            </p>
          )}
          <Button type="submit" variant="primary" className="w-full" disabled={pending}>
            {pending ? 'Signing in…' : 'Sign in'}
          </Button>
        </form>

        <p className="mt-4 text-xs text-slate-500">
          Credentials are sent only to <span className="font-mono">{API_BASE_URL}</span>. The access token is kept in
          memory and discarded when you sign out or reload the page.
        </p>
      </Card>
    </div>
  )
}
