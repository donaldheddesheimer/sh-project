import type { ModelCall } from '../api/types'
import { clock } from '../lib/format'
import { Icon, type IconName } from './Icon'

const ICON: Record<ModelCall['status'], IconName> = { pending: 'spinner', ok: 'check', error: 'error' }

/** "nvidia/nemotron-3-ultra-550b-a55b" -> "nemotron-3-ultra-550b-a55b"; the full id stays in the tooltip. */
const shortModel = (model: string) => model.split('/').at(-1) ?? model

const tokens = (call: ModelCall) =>
  call.prompt_tokens == null && call.completion_tokens == null
    ? null
    : `${call.prompt_tokens ?? '?'}→${call.completion_tokens ?? '?'} tok`

/**
 * Whether the hosted models are actually being called, and what came back. The operations log shows what the
 * agent decided; this shows the requests behind it, so "Nemotron proposed nothing" can be told apart from
 * "Nemotron was never called" and from "Nemotron answered 503".
 */
export function ModelCallLog({ calls }: { calls: ModelCall[] }) {
  const newestFirst = [...calls].reverse()
  const failures = calls.filter((call) => call.status === 'error').length
  return (
    <div className="model-log">
      <div className="subhead">
        <Icon name="bolt" size={12} /> Model calls <span className="subhead-meta">{calls.length}</span>
        {failures > 0 && <span className="subhead-meta model-log-failed">{failures} failed</span>}
      </div>
      {calls.length === 0 ? (
        <p className="model-log-empty">
          No model call yet. The analyst is called when an episode analyzes an incident, or when Analyze
          Response runs with a model provider configured.
        </p>
      ) : (
        <ol>
          {newestFirst.map((call) => (
            <li key={call.id} className={`model-call is-${call.status}`}>
              <span className="model-call-time mono">{call.sim_time != null ? clock(call.sim_time) : '--:--:--'}</span>
              <span className="model-call-icon" aria-label={call.status}>
                <Icon name={ICON[call.status]} size={12} />
              </span>
              <div className="model-call-body">
                <div className="model-call-head">
                  <strong>{call.provider}</strong>
                  <span className="tag tag-minimal">{call.purpose}</span>
                  <span className="muted">{call.role}</span>
                  <span className="model-call-model mono" title={`${call.model} · ${call.endpoint}`}>
                    {shortModel(call.model)}
                  </span>
                </div>
                <div className="model-call-meta">
                  {call.status === 'pending' ? (
                    <span>waiting for a reply…</span>
                  ) : (
                    <span>{call.duration_ms == null ? '—' : `${(call.duration_ms / 1000).toFixed(1)} s`}</span>
                  )}
                  {call.http_status != null && <span>HTTP {call.http_status}</span>}
                  {call.attempts > 1 && <span>{call.attempts} attempts</span>}
                  {tokens(call) && <span>{tokens(call)}</span>}
                </div>
                {call.detail && <p className="model-call-detail">{call.detail}</p>}
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}
