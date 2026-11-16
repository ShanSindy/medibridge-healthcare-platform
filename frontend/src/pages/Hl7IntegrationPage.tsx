import { Badge } from '../components/ui/Badge'
import { Card } from '../components/ui/Card'
import { Icon, type IconName } from '../components/ui/Icon'
import { Callout, CodeBlock } from '../components/ui/Misc'
import { EmptyState } from '../components/ui/States'

const PIPELINE: Array<{ icon: IconName; title: string; detail: string }> = [
  { icon: 'arrows', title: 'Receive', detail: 'Inbound message posted to POST /hl7/adt.' },
  { icon: 'file', title: 'Parse', detail: 'MSH header and PID patient segment are extracted.' },
  { icon: 'check', title: 'Validate', detail: 'Message type, control ID and required fields are checked.' },
  { icon: 'database', title: 'Apply', detail: 'Patient is registered (A04) or updated (A08).' },
  {
    icon: 'shield',
    title: 'Track & ACK',
    detail: 'Message is recorded for tracking; an AA/AE/AR acknowledgement is returned on request.',
  },
]

const MESSAGE_TYPES = [
  { type: 'ADT^A04', name: 'Register a patient', supported: true },
  { type: 'ADT^A08', name: 'Update patient information', supported: true },
  { type: 'Other ADT / ORM / ORU / SIU', name: 'Rejected as unsupported', supported: false },
]

const SAMPLE_MESSAGE = `MSH|^~\\&|DEMO_REG|NORTHSIDE|MEDIBRIDGE|MEDIBRIDGE|20261115083000||ADT^A04|DEMO000123|P|2.5.1
EVN|A04|20261115083000
PID|1||DEMO-100221^^^MEDIBRIDGE^MR||Farouk^Leila||19930406|F
PV1|1|O`

export function Hl7IntegrationPage() {
  return (
    <div className="space-y-6">
      <Callout title="Planned feature — message monitoring not connected" tone="info">
        HL7 v2 ADT processing is implemented in the MediBridge backend. This page will show message throughput,
        failures and acknowledgements once the dashboard is connected to the API. Nothing is sent or received from
        this page today.
      </Callout>

      <Card title="Processing pipeline" description="How the backend handles an inbound ADT message">
        <ol className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          {PIPELINE.map((step, index) => (
            <li key={step.title} className="relative rounded-lg border border-slate-200 bg-slate-50/60 p-4">
              <div className="flex items-center gap-2">
                <span className="flex size-7 items-center justify-center rounded-md bg-white text-brand-700 ring-1 ring-slate-200">
                  <Icon name={step.icon} className="size-4" />
                </span>
                <span className="text-xs font-semibold text-slate-400 tabular-nums">Step {index + 1}</span>
              </div>
              <p className="mt-3 text-sm font-semibold text-navy-900">{step.title}</p>
              <p className="mt-1 text-sm text-slate-500">{step.detail}</p>
            </li>
          ))}
        </ol>
      </Card>

      <div className="grid gap-6 lg:grid-cols-5">
        <Card title="Supported message types" className="lg:col-span-2" bodyClassName="p-0">
          <ul className="divide-y divide-slate-100">
            {MESSAGE_TYPES.map((message) => (
              <li key={message.type} className="flex items-center justify-between gap-3 px-5 py-3.5">
                <div>
                  <p className="font-mono text-sm font-medium text-navy-900">{message.type}</p>
                  <p className="text-sm text-slate-500">{message.name}</p>
                </div>
                {message.supported ? (
                  <Badge tone="success" dot>
                    Supported
                  </Badge>
                ) : (
                  <Badge tone="neutral" dot>
                    Not supported
                  </Badge>
                )}
              </li>
            ))}
          </ul>
        </Card>

        <Card
          title="Sample ADT^A04 message"
          description="Synthetic example for illustration — not a real patient."
          className="lg:col-span-3"
        >
          <CodeBlock label="HL7 v2.5.1 (pipe-delimited)" code={SAMPLE_MESSAGE} />
        </Card>
      </div>

      <Card title="Message log" description="Recent inbound messages and acknowledgements">
        <EmptyState
          icon="inbox"
          title="Message log not connected"
          description="Processed messages, failures and ACK codes will be listed here once the dashboard reads from the backend’s HL7 message tracking."
        />
      </Card>
    </div>
  )
}
