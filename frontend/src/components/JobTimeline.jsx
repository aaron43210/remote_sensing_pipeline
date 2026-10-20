import { CheckCircle2, CircleAlert, CircleSlash, Clock, Loader2 } from 'lucide-react'
import { duration, when } from '../lib/format'
import { STATUS_TONE, TASKS } from '../lib/layers'

const STAGES = ['ingest', 'preprocess', 'inference', 'done']
const STAGE_LABEL = { queued: 'Queued', ingest: 'Fetching imagery', preprocess: 'Cleaning spectra',
  inference: 'Running HydraNet', done: 'Done' }

function stepClass(task, i) {
  const idx = STAGES.indexOf(task.stage)
  if (task.state === 'DONE') return 'done'
  if (['ERROR', 'TIMED_OUT'].includes(task.state)) return i <= Math.max(idx, 0) ? (i === Math.max(idx, 0) ? 'fail' : 'done') : ''
  if (task.state === 'NO_DATA' || task.state === 'CANCELLED') return i < idx ? 'done' : ''
  if (i < idx) return 'done'
  if (i === idx && task.state === 'RUNNING') return 'active'
  return ''
}

function StateIcon({ state }) {
  if (state === 'DONE') return <CheckCircle2 size={14} color="var(--good)" aria-label="done" />
  if (state === 'ERROR' || state === 'TIMED_OUT') return <CircleAlert size={14} color="var(--critical)" aria-label="failed" />
  if (state === 'NO_DATA' || state === 'CANCELLED') return <CircleSlash size={14} color="var(--text-muted)" aria-label={state} />
  if (state === 'RUNNING') return <Loader2 size={14} className="spin" aria-label="running" />
  return <Clock size={14} color="var(--text-muted)" aria-label="pending" />
}

const STATUS_TEXT = { QUEUED: 'Queued', RUNNING: 'Running', COMPLETED: 'Completed', PARTIAL: 'Partly completed',
  FAILED: 'Failed', NO_DATA: 'No imagery', CANCELLED: 'Cancelled' }

export default function JobTimeline({ job, onCancel }) {
  const elapsed = ((job.completed_at ? new Date(job.completed_at) : new Date()) - new Date(job.created_at)) / 1000
  return (
    <div className="card timeline" aria-live="polite">
      <div className="row between">
        <h2 style={{ margin: 0 }}>Job <span className="num muted" style={{ fontWeight: 400 }}>{job.job_id.slice(4, 12)}</span></h2>
        <span className={`badge ${STATUS_TONE[job.status] || ''}`}>{STATUS_TEXT[job.status] || job.status}</span>
      </div>
      {job.reused_from && <div className="notice info">An identical request was already processed; its maps were reused.</div>}
      <div className="progress" aria-label={`progress ${job.progress}%`}><div style={{ width: `${job.progress}%` }} /></div>
      <div className="row between muted" style={{ fontSize: 12 }}>
        <span>Started {when(job.created_at)}</span>
        <span className="num">{duration(elapsed)}</span>
      </div>
      {job.queue_position && <p className="secondary" style={{ fontSize: 12 }}>Position in queue: {job.queue_position}</p>}
      {(job.task_states || []).map((t) => (
        <div className="trow" key={t.task}>
          <div className="row" style={{ gap: 6 }}><StateIcon state={t.state} /><strong>{TASKS[t.task]?.label || t.task}</strong></div>
          <div>
            <div className="steps">{STAGES.map((s, i) => <div key={s} className={`step ${stepClass(t, i)}`} title={STAGE_LABEL[s]} />)}</div>
            <div className="secondary" style={{ fontSize: 12, marginTop: 4 }}>
              {t.state === 'DONE' ? 'Maps ready' : t.state === 'NO_DATA' ? `No imagery: ${t.message}`
                : t.state === 'ERROR' || t.state === 'TIMED_OUT' ? `Failed: ${t.message}`
                  : t.state === 'CANCELLED' ? 'Cancelled' : (t.message || STAGE_LABEL[t.stage])}
            </div>
          </div>
        </div>
      ))}
      {job.error && job.final && job.status !== 'COMPLETED' && <div className="notice error">{job.error}</div>}
      {!job.final && onCancel && <button className="btn small danger" onClick={onCancel}>Cancel job</button>}
    </div>
  )
}
