import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { Plus } from 'lucide-react'
import { api, watchJob } from '../api'
import AreaMap from '../components/AreaMap'
import JobForm from '../components/JobForm'
import JobTimeline from '../components/JobTimeline'
import PixelInspector from '../components/PixelInspector'
import ResultsPanel from '../components/ResultsPanel'
import { forgetCogs } from '../lib/cog'
import { useServerConfig } from '../useServerConfig'

export default function Workspace() {
  const { id } = useParams()
  const navigate = useNavigate()
  const config = useServerConfig()
  const [bbox, setBbox] = useState(null)
  const [drawing, setDrawing] = useState(false)
  const [job, setJob] = useState(null)
  const [data, setData] = useState(null)
  const [overlays, setOverlays] = useState([])
  const [point, setPoint] = useState(null)
  const [error, setError] = useState(null)

  // Follow the job live (SSE, polling fallback) while it runs.
  useEffect(() => {
    setJob(null); setData(null); setOverlays([]); setPoint(null); setError(null)
    if (!id) return undefined
    let stop = () => {}
    api.job(id).then((j) => {
      setJob(j); setBbox(j.bbox)
      if (!j.final) stop = watchJob(id, setJob)
    }).catch((e) => setError(e.status === 404 ? 'This job does not exist or is not yours.' : e.message))
    return () => stop()
  }, [id])

  const loadLayers = useCallback(() => {
    if (!id) return
    forgetCogs()
    api.layers(id).then(setData).catch((e) => setError(e.message))
  }, [id])

  const hasMaps = job && job.task_states?.some((t) => t.state === 'DONE')
  const finalAndReady = job && (job.final || hasMaps)
  useEffect(() => { if (finalAndReady) loadLayers() }, [finalAndReady, job?.status, loadLayers])

  // Presigned URLs expire: refresh them shortly before they do.
  useEffect(() => {
    if (!data?.expires_in_s) return undefined
    const t = setTimeout(loadLayers, Math.max(60, data.expires_in_s - 120) * 1000)
    return () => clearTimeout(t)
  }, [data, loadLayers])

  const onOverlay = useCallback((o) => setOverlays(o), [])
  const cancel = async () => { try { setJob(await api.cancelJob(id)) } catch (e) { setError(e.message) } }

  if (!config) return <div className="page muted">Loading…</div>
  return (
    <div className="workspace">
      <aside className="sidebar">
        {id ? (
          <>
            <button className="btn" onClick={() => navigate('/')}><Plus size={14} /> New area</button>
            {error && <div className="notice error">{error}</div>}
            {job && <JobTimeline job={job} onCancel={cancel} />}
            {job && data && <ResultsPanel job={job} data={data} onOverlay={onOverlay} onRefresh={loadLayers} />}
          </>
        ) : (
          <>
            <div>
              <h1>Make maps for an area</h1>
              <p className="secondary">One neural network (HydraNet) produces agriculture, mineral and thermal maps.
                Each map is checked against the classical method it reproduces.</p>
            </div>
            <JobForm config={config} bbox={bbox} setBbox={setBbox} drawing={drawing} setDrawing={setDrawing}
                     onSubmitted={(j) => navigate(`/jobs/${j.job_id}`)} />
          </>
        )}
      </aside>
      <AreaMap region={config.allowed_region} maxDeg={config.max_bbox_deg} bbox={bbox} onBbox={setBbox}
               drawing={drawing} setDrawing={setDrawing} overlays={overlays} marker={point}
               onInspect={data ? (lat, lon) => setPoint([lat, lon]) : null}>
        {data && !point && <div className="map-hint">Click the map to read every value at a point</div>}
        {data && <PixelInspector point={point} layers={data.layers} onClose={() => setPoint(null)} />}
      </AreaMap>
    </div>
  )
}
