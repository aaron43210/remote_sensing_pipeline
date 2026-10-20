import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { api } from '../api'
import { bboxLabel, when } from '../lib/format'
import { STATUS_TONE, TASKS } from '../lib/layers'

export default function History() {
  const [jobs, setJobs] = useState(null)
  const [next, setNext] = useState(null)
  const [error, setError] = useState(null)
  const load = (before) => api.jobs(before).then((r) => {
    setJobs((cur) => (before ? [...(cur || []), ...r.jobs] : r.jobs))
    setNext(r.next_before || null)
  }).catch((e) => setError(e.message))
  useEffect(() => { load() }, [])
  return (
    <div className="page"><div className="page-narrow">
      <h1>Your jobs</h1>
      <p className="secondary">Every area you have mapped. Results are kept for 180 days.</p>
      {error && <div className="notice error">{error}</div>}
      <div className="card" style={{ marginTop: 12 }}>
        {!jobs ? <p className="muted">Loading…</p> : jobs.length === 0 ? (
          <p className="muted">No jobs yet. <Link to="/">Map your first area</Link>.</p>
        ) : (
          <table className="data">
            <thead><tr><th>Submitted</th><th>Area</th><th>Maps</th><th>Source</th><th>Status</th><th /></tr></thead>
            <tbody>{jobs.map((j) => (
              <tr key={j.job_id}>
                <td>{when(j.created_at)}</td>
                <td className="num secondary" style={{ fontSize: 12 }}>{bboxLabel(j.bbox)}</td>
                <td>{j.tasks.map((t) => TASKS[t]?.label || t).join(', ')}</td>
                <td>{j.source === 'fixture' ? <span className="badge warning">test scene</span> : 'NASA'}</td>
                <td><span className={`badge ${STATUS_TONE[j.status] || ''}`}>{j.status.toLowerCase().replace('_', ' ')}</span></td>
                <td className="r"><Link to={`/jobs/${j.job_id}`}>Open</Link></td>
              </tr>
            ))}</tbody>
          </table>
        )}
        {next && <button className="btn" style={{ marginTop: 10 }} onClick={() => load(next)}>Load older</button>}
      </div>
    </div></div>
  )
}
