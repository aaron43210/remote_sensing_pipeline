import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, Crosshair, Play } from 'lucide-react'
import { api, ApiError, newIdempotencyKey } from '../api'
import { scenesCovering } from '../lib/coverage'
import { bboxAreaKm2, dateOnly, todayIso } from '../lib/format'
import { TASKS } from '../lib/layers'
import EarthdataDialog from './EarthdataDialog'

// Maps made from NASA EMIT: downloaded with the user's own Earthdata token.
const EMIT_TASKS = ['agriculture', 'mineral']

function validBox(b, region, maxDeg) {
  if (!b || b.some((v) => !Number.isFinite(v))) return 'Enter four numbers'
  if (b[0] >= b[2] || b[1] >= b[3]) return 'West must be less than east and south less than north'
  if (b[2] - b[0] > maxDeg + 1e-9 || b[3] - b[1] > maxDeg + 1e-9) return `At most ${maxDeg}° per side`
  if (b[2] - b[0] < 0.002 || b[3] - b[1] < 0.002) return 'At least 0.002° per side'
  if (b[0] < region[0] || b[1] < region[1] || b[2] > region[2] || b[3] > region[3]) return 'Outside the supported region'
  return null
}

export default function JobForm({ config, bbox, setBbox, drawing, setDrawing, onSubmitted, dates, setDates, coverage }) {
  // Thermal needs no account; agriculture and minerals are on by default once NASA is connected.
  const [tasks, setTasks] = useState(['thermal'])
  const [nasa, setNasa] = useState(null)              // { connected, expired, username, expires_at }
  const [connectFor, setConnectFor] = useState(null)  // why the NASA popup is open: a task, 'submit' or 'change'
  const [dateFrom, dateTo] = dates
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [edit, setEdit] = useState(null)
  const idemKey = useMemo(newIdempotencyKey, [bbox, tasks, dateFrom, dateTo])
  const needsEmit = tasks.includes('agriculture') || tasks.includes('mineral')
  const scenes = useMemo(() => scenesCovering(coverage, bbox), [coverage, bbox])

  useEffect(() => {
    api.earthdata().then((s) => {
      setNasa(s)
      if (s.connected) setTasks((cur) => [...new Set([...cur, ...EMIT_TASKS])])
    }).catch(() => setNasa({ connected: false }))
  }, [])

  const boxError = bbox ? validBox(bbox, config.allowed_region, config.max_bbox_deg) : null
  const toggle = (t) => {
    if (!tasks.includes(t) && EMIT_TASKS.includes(t) && !nasa?.connected) {
      setConnectFor(t)                                // ask for the token first; ticked once connected
      return
    }
    setTasks((cur) => (cur.includes(t) ? cur.filter((x) => x !== t) : [...cur, t]))
  }

  async function submit(e) {
    e?.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const job = await api.createJob({ bbox, tasks, date_from: dateFrom, date_to: dateTo }, idemKey)
      onSubmitted(job)
    } catch (err) {
      if (err instanceof ApiError && err.body?.code === 'earthdata_required') {   // missing or expired since
        api.earthdata().then(setNasa).catch(() => {})
        setConnectFor('submit')
      } else {
        setError(err instanceof ApiError && err.status === 429
          ? `${err.message}${err.retryAfter ? ` (retry in ${err.retryAfter} s)` : ''}` : err.message)
      }
    } finally {
      setBusy(false)
    }
  }

  const connectDone = (status) => {
    const why = connectFor
    setConnectFor(null)
    if (status) {
      setNasa(status)
      if (EMIT_TASKS.includes(why)) setTasks((cur) => (cur.includes(why) ? cur : [...cur, why]))
      if (why === 'submit') submit()
    } else if (why !== 'change') {
      setTasks(['thermal'])                           // "Temperature results only"
    }
  }

  const disconnect = async () => {
    try {
      await api.disconnectEarthdata()
      setNasa({ connected: false })
      setTasks(['thermal'])
    } catch (err) {
      setError(err.message)
    }
  }

  const startEdit = () => setEdit((bbox || [0, 0, 0, 0]).map((v) => v.toFixed(4)))
  const applyEdit = () => {
    const b = edit.map(Number)
    const err = validBox(b, config.allowed_region, config.max_bbox_deg)
    if (err) { setError(err); return }
    setError(null); setBbox(b); setEdit(null)
  }

  return (
    <>
      <form className="card" onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div>
          <h2>1. Area</h2>
          {bbox ? (
            <p className="secondary num" style={{ fontSize: 12 }}>
              {bbox.map((v) => v.toFixed(4)).join(', ')} · ≈ {bboxAreaKm2(bbox).toFixed(0)} km²
            </p>
          ) : <p className="muted">Draw a box on the map, or type its corners.</p>}
          {boxError && <p style={{ color: 'var(--critical)', fontSize: 12 }}>{boxError}</p>}
          {edit ? (
            <div className="grid2" style={{ marginTop: 6 }}>
              {['West (lon)', 'South (lat)', 'East (lon)', 'North (lat)'].map((lbl, i) => (
                <label className="field" key={lbl}>{lbl}
                  <input type="number" step="0.0001" value={edit[i]} onChange={(e) => {
                    const n = [...edit]; n[i] = e.target.value; setEdit(n)
                  }} />
                </label>
              ))}
              <button type="button" className="btn" onClick={() => setEdit(null)}>Cancel</button>
              <button type="button" className="btn primary" onClick={applyEdit}>Apply</button>
            </div>
          ) : (
            <div className="row" style={{ marginTop: 6 }}>
              <button type="button" className={`btn ${drawing ? 'primary' : ''}`} onClick={() => setDrawing(!drawing)}>
                <Crosshair size={14} /> {drawing ? 'Drawing… (Esc)' : 'Draw area'}
              </button>
              <button type="button" className="btn" onClick={startEdit}>Enter coordinates</button>
            </div>
          )}
        </div>

        <div>
          <h2>2. Maps</h2>
          <div className="row wrap">
            {config.tasks.map((t) => (
              <label key={t} className={`chip ${tasks.includes(t) ? 'on' : ''}`} title={TASKS[t].blurb}>
                <input type="checkbox" checked={tasks.includes(t)} onChange={() => toggle(t)} /> {TASKS[t].label}
              </label>
            ))}
          </div>
          {nasa?.connected ? (
            <p className="muted" style={{ fontSize: 12, marginTop: 6 }}>
              <CheckCircle2 size={12} style={{ verticalAlign: -2, color: 'var(--good)' }} /> NASA Earthdata connected
              {nasa.username && ` as ${nasa.username}`}{nasa.expires_at && `, until ${dateOnly(nasa.expires_at)}`} ·{' '}
              <button type="button" className="link" onClick={() => setConnectFor('change')}>Change</button> ·{' '}
              <button type="button" className="link" onClick={disconnect}>Disconnect</button>
            </p>
          ) : nasa && (
            <p className="muted" style={{ fontSize: 12, marginTop: 6 }}>
              {nasa.expired && `Your NASA token expired on ${dateOnly(nasa.expires_at)}. `}
              Agriculture and Minerals use your free NASA Earthdata account; Thermal needs none.
            </p>
          )}
        </div>

        <div>
          <h2>3. Imagery dates</h2>
          <div className="grid2">
            <label className="field">From<input type="date" value={dateFrom} max={dateTo} onChange={(e) => e.target.value && setDates([e.target.value, dateTo])} /></label>
            <label className="field">To<input type="date" value={dateTo} min={dateFrom} max={todayIso()} onChange={(e) => e.target.value && setDates([dateFrom, e.target.value])} /></label>
          </div>
          <p className="muted" style={{ fontSize: 12, marginTop: 6 }}>
            The clearest scene in this window is used: EMIT for agriculture and minerals, Landsat 8/9 for thermal.
          </p>
          {bbox && !boxError && needsEmit && coverage && (scenes.length > 0 ? (
            <div className="notice good" role="status"><CheckCircle2 size={14} /><span>
              {`${scenes.length} EMIT scene${scenes.length > 1 ? 's' : ''} cover this area in these dates. `}
              {`Will use ${scenes[0].acquired_at} (${Math.round(scenes[0].cloud_pct ?? 0)} % cloud)`}
              {scenes[0].staged ? ', already downloaded: maps in seconds.' : ', first use downloads it from NASA (about 10 min).'}
            </span></div>
          ) : (
            <div className="notice warning" role="status"><AlertTriangle size={14} /><span>
              No EMIT scene covers this whole area in these dates, so agriculture and minerals would have no data.
              Move it inside the bright, white-outlined area (one scene must cover the whole box) or widen the dates. Thermal (Landsat) works everywhere.
            </span></div>
          ))}
        </div>

        {error && <div className="notice error" role="alert">{error}</div>}
        <button className="btn primary block" disabled={busy || !bbox || !!boxError || tasks.length === 0}>
          <Play size={14} /> {busy ? 'Submitting…' : 'Make maps'}
        </button>
      </form>
      {/* outside the form: it has its own (a token field must not submit the job) */}
      <EarthdataDialog reason={connectFor} status={nasa} onDone={connectDone} />
    </>
  )
}
