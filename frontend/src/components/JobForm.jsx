import { useEffect, useMemo, useState } from 'react'
import { Crosshair, Play } from 'lucide-react'
import { api, ApiError, newIdempotencyKey } from '../api'
import { bboxAreaKm2, dateOnly } from '../lib/format'
import { TASKS } from '../lib/layers'

const today = () => new Date().toISOString().slice(0, 10)
const yearAgo = () => new Date(Date.now() - 365 * 864e5).toISOString().slice(0, 10)

function validBox(b, region, maxDeg) {
  if (!b || b.some((v) => !Number.isFinite(v))) return 'Enter four numbers'
  if (b[0] >= b[2] || b[1] >= b[3]) return 'West must be less than east and south less than north'
  if (b[2] - b[0] > maxDeg + 1e-9 || b[3] - b[1] > maxDeg + 1e-9) return `At most ${maxDeg}° per side`
  if (b[2] - b[0] < 0.002 || b[3] - b[1] < 0.002) return 'At least 0.002° per side'
  if (b[0] < region[0] || b[1] < region[1] || b[2] > region[2] || b[3] > region[3]) return 'Outside the supported region'
  return null
}

export default function JobForm({ config, bbox, setBbox, drawing, setDrawing, onSubmitted }) {
  const [tasks, setTasks] = useState(['agriculture', 'mineral', 'thermal'])
  const [dateFrom, setDateFrom] = useState(yearAgo())
  const [dateTo, setDateTo] = useState(today())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [catalog, setCatalog] = useState(null)
  const [edit, setEdit] = useState(null)
  const idemKey = useMemo(newIdempotencyKey, [bbox, tasks, dateFrom, dateTo])

  useEffect(() => {
    setCatalog(null)
    if (!bbox) return undefined
    let live = true
    api.catalog(bbox).then((c) => live && setCatalog(c.granules)).catch(() => live && setCatalog([]))
    return () => { live = false }
  }, [bbox])

  const boxError = bbox ? validBox(bbox, config.allowed_region, config.max_bbox_deg) : null
  const toggle = (t) => setTasks((cur) => (cur.includes(t) ? cur.filter((x) => x !== t) : [...cur, t]))

  async function submit(e) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const job = await api.createJob({ bbox, tasks, date_from: dateFrom, date_to: dateTo }, idemKey)
      onSubmitted(job)
    } catch (err) {
      setError(err instanceof ApiError && err.status === 429
        ? `${err.message}${err.retryAfter ? ` (retry in ${err.retryAfter} s)` : ''}` : err.message)
    } finally {
      setBusy(false)
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
      </div>

      <div>
        <h2>3. Imagery dates</h2>
        <div className="grid2">
          <label className="field">From<input type="date" value={dateFrom} max={dateTo} onChange={(e) => setDateFrom(e.target.value)} /></label>
          <label className="field">To<input type="date" value={dateTo} min={dateFrom} max={today()} onChange={(e) => setDateTo(e.target.value)} /></label>
        </div>
        <p className="muted" style={{ fontSize: 12, marginTop: 6 }}>
          The clearest scene in this window is used: EMIT for agriculture and minerals, Landsat 8/9 for thermal.
        </p>
        {catalog && catalog.length > 0 && (
          <p className="secondary" style={{ fontSize: 12 }}>
            Already indexed here: {catalog.slice(0, 3).map((g) => `${g.sensor} ${dateOnly(g.acquired_at)}`).join(' · ')}
            {catalog.length > 3 ? ` +${catalog.length - 3} more` : ''}
          </p>
        )}
      </div>

      {error && <div className="notice error" role="alert">{error}</div>}
      <button className="btn primary block" disabled={busy || !bbox || !!boxError || tasks.length === 0}>
        <Play size={14} /> {busy ? 'Submitting…' : 'Make maps'}
      </button>
    </form>
  )
}
