import { useEffect, useMemo, useState } from 'react'
import { CheckCircle2, CircleAlert, Download } from 'lucide-react'
import { renderCategorical } from '../lib/cog'
import { dateOnly, fmt, pct } from '../lib/format'
import { layerLabel, orderLayers, TASKS } from '../lib/layers'
import { CompositionBars, Histogram, Legend, StatTile } from './charts'

// Agreement the deployed network must keep with the classical method (shadow check).
const SHADOW_GATE = { pixel_agreement: 0.97, r2: 0.95, mae: 0.05 }

function Verification({ verification }) {
  const rows = Object.entries(verification || {})
  if (!rows.length) return null
  return (
    <details className="card" style={{ padding: '10px 12px', overflow: 'hidden' }}>
      <summary style={{ cursor: 'pointer', outline: 'none' }}>
        <strong>Checked against the classical method</strong>
        <span className="muted" style={{ fontSize: 12 }}> · on a sample of this area&apos;s pixels</span>
      </summary>
      <div className="table-wrap" style={{ marginTop: 8 }}>
        <table className="data">
          <thead>
            <tr>
              <th>Map</th>
              <th className="r">Agreement</th>
              <th className="r">Pixels</th>
              <th className="r">Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([layer, m]) => {
              const metric = m.pixel_agreement !== undefined ? 'pixel_agreement' : m.r2 !== undefined && m.r2 !== null ? 'r2' : 'mae'
              const v = m[metric]
              const ok = metric === 'mae' ? v <= SHADOW_GATE.mae : v >= SHADOW_GATE[metric]
              return (
                <tr key={layer}>
                  <td style={{ fontWeight: 500 }}>{layerLabel(layer)}</td>
                  <td className="r num" style={{ whiteSpace: 'nowrap' }}>
                    {metric === 'pixel_agreement' ? pct(v) : `${metric.toUpperCase()} ${fmt(v, 3)}`}
                  </td>
                  <td className="r num muted" style={{ whiteSpace: 'nowrap' }}>
                    {m.n?.toLocaleString() ?? m.n}
                  </td>
                  <td className="r" style={{ whiteSpace: 'nowrap', width: 72 }}>
                    {ok ? (
                      <span className="badge good" style={{ padding: '2px 6px', fontSize: 11 }}>
                        <CheckCircle2 size={11} /> same
                      </span>
                    ) : (
                      <span className="badge critical" style={{ padding: '2px 6px', fontSize: 11 }}>
                        <CircleAlert size={11} /> differs
                      </span>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </details>
  )
}

function Summary({ task, layer, layers, result }) {
  const s = result?.summary || {}
  if (task === 'thermal') {
    const lst = layers.find((l) => l.name === 'thermal_lst')?.stats || {}
    return (
      <div className="grid2">
        <StatTile label="Urban heat island" value={`${fmt(s.uhi_intensity_c, 1)} °C`} sub="urban minus rural mean" />
        <StatTile label="Hotspots" value={s.hotspot_count ?? '–'} sub={`${s.hotspot_pixels ?? 0} pixels`} />
        <StatTile label="Median temperature" value={`${fmt(lst.p50, 1)} °C`} sub={`${fmt(lst.p5, 1)} to ${fmt(lst.p95, 1)} °C (5–95 %)`} />
        <StatTile label="Clear pixels" value={pct(lst.valid_fraction, 0)} sub="cloud & shadow free" />
      </div>
    )
  }
  if (task === 'mineral') {
    const frac = layers.find((l) => l.name === 'mineral_class')?.stats?.class_fraction || {}
    const top = Object.entries(frac).filter(([c]) => c !== 'unknown').sort((a, b) => b[1] - a[1])[0]
    return (
      <div className="grid2">
        <StatTile label="Bare ground mapped" value={pct(s.mapped_fraction, 0)} sub="vegetation and water excluded" />
        <StatTile label="Most common mineral" value={top ? top[0] : '–'} sub={top ? pct(top[1]) : 'none'} />
      </div>
    )
  }
  const st = layer?.stats || {}
  return (
    <div className="grid2">
      <StatTile label="Vegetation mapped" value={pct(s.mapped_fraction, 0)} sub="NDVI ≥ 0.25, cloud-free" />
      {layer?.kind === 'continuous' && (
        <StatTile label={`Median ${layerLabel(layer.name).split(' (')[0].toLowerCase()}`}
                  value={`${fmt(st.p50)} ${layer.units}`} sub={`${fmt(st.p5)} to ${fmt(st.p95)} (5–95 %)`} />
      )}
    </div>
  )
}

export default function ResultsPanel({ job, data, onOverlay, onRefresh }) {
  const tasks = useMemo(() => job.tasks.filter((t) => data.layers.some((l) => l.task === t)), [job, data])
  const [picked, setPicked] = useState(null)   // a tab the user chose; otherwise follow task order
  const task = picked && tasks.includes(picked) ? picked : tasks[0]
  const setTask = setPicked
  const [layerName, setLayerName] = useState(null)
  const [opacity, setOpacity] = useState(0.85)
  const [highlight, setHighlight] = useState(null)
  const [rendered, setRendered] = useState(null)

  const list = useMemo(() => orderLayers(data.layers, task), [data, task])
  const layer = list.find((l) => l.name === layerName) || list[0]
  useEffect(() => { setLayerName(list[0]?.name || null); setHighlight(null) }, [task]) // eslint-disable-line

  // Mineral highlight is drawn client-side from the class COG; everything else uses the PNG.
  useEffect(() => {
    let live = true
    setRendered(null)
    if (layer?.name === 'mineral_class' && highlight) {
      const idx = layer.classes.indexOf(highlight)
      renderCategorical(layer.tif_url, layer.stats.palette, idx).then((url) => live && setRendered(url)).catch(() => {})
    }
    return () => { live = false }
  }, [layer, highlight])

  useEffect(() => {
    onOverlay(layer ? [{ key: `${layer.task}/${layer.name}/${highlight || ''}/${rendered ? 1 : 0}`,
      url: rendered || layer.png_url, bounds: layer.bounds, opacity, onError: onRefresh }] : [])
  }, [layer, opacity, rendered, highlight, onOverlay, onRefresh])

  if (!tasks.length) return <div className="card"><p className="muted">No maps were produced for this job.</p></div>
  const result = data.results?.[task]
  const acquired = task === 'thermal' ? job.acquired_landsat : job.acquired_emit
  const classRows = layer?.kind === 'categorical' ? (layer.classes || []).map((c, i) => ({
    name: c, color: layer.stats.palette[i], fraction: layer.stats.class_fraction?.[c] || 0,
    pixels: layer.stats.class_pixels?.[c], group: layer.stats.groups?.[c],
  })).filter((r) => r.color !== '#00000000').sort((a, b) => b.fraction - a.fraction) : []

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div className="tabs" role="tablist">
        {tasks.map((t) => (
          <button key={t} role="tab" aria-selected={t === task} className={t === task ? 'active' : ''} onClick={() => setTask(t)}>
            {TASKS[t].label}
          </button>
        ))}
      </div>
      <p className="muted" style={{ fontSize: 12, margin: 0 }}>
        {task === 'thermal' ? 'Landsat 8/9' : 'NASA EMIT'} · acquired {dateOnly(acquired)} · HydraNet {result?.model_version || job.model_version}
        {result?.summary?.granule_id ? ` · ${result.summary.granule_id}` : ''}
      </p>

      <label className="field">Map layer
        <select value={layer?.name || ''} onChange={(e) => { setLayerName(e.target.value); setHighlight(null) }}>
          {list.map((l) => <option key={l.name} value={l.name}>{layerLabel(l.name)}</option>)}
        </select>
      </label>
      <label className="field">Opacity {Math.round(opacity * 100)} %
        <input type="range" min="0.1" max="1" step="0.05" value={opacity} onChange={(e) => setOpacity(Number(e.target.value))} />
      </label>

      <div>
        <h3>{layerLabel(layer?.name || '')}</h3>
        <Legend layer={layer} />
      </div>

      {layer?.name === 'mineral_class' && (
        <label className="field">Highlight one mineral
          <select value={highlight || ''} onChange={(e) => setHighlight(e.target.value || null)}>
            <option value="">All minerals (coloured by group)</option>
            {classRows.filter((r) => r.fraction > 0).map((r) => <option key={r.name} value={r.name}>{r.name} ({pct(r.fraction)})</option>)}
          </select>
        </label>
      )}

      <Summary task={task} layer={layer} layers={list} result={result} />

      {layer?.kind === 'categorical'
        ? <CompositionBars rows={classRows} onPick={layer.name === 'mineral_class' ? setHighlight : null} picked={highlight} />
        : <Histogram histogram={layer?.stats?.histogram} units={layer?.units}
                     color={layer?.colormap === 'orange' ? 'var(--series-2)' : 'var(--series-1)'} />}

      <Verification verification={result?.verification} />

      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, paddingTop: 6, borderTop: '1px solid var(--border)' }}>
        <div className="row between">
          <a className="btn small" href={layer?.tif_url} download><Download size={13} /> GeoTIFF (this layer)</a>
          <span className="muted num" style={{ fontSize: 11 }}>EPSG:4326 · Cloud-optimised</span>
        </div>
        <span className="muted" style={{ fontSize: 11 }}>Direct raster layer download for QGIS, ArcGIS, or GDAL.</span>
      </div>
    </div>
  )
}
