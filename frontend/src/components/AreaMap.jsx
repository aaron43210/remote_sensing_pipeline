import { useEffect, useMemo, useRef, useState } from 'react'
import L from 'leaflet'
import { GeoJSON, ImageOverlay, MapContainer, Polygon, Rectangle, TileLayer, useMap, useMapEvents } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'

const BASEMAPS = {
  imagery: {
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Imagery © Esri, Maxar, Earthstar Geographics',
  },
  streets: {
    url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution: '© OpenStreetMap contributors',
  },
}

const toBounds = (b) => [[b[1], b[0]], [b[3], b[2]]]

function clampBox(start, end, maxDeg) {
  const lat = start.lat + Math.sign(end.lat - start.lat) * Math.min(Math.abs(end.lat - start.lat), maxDeg)
  const lng = start.lng + Math.sign(end.lng - start.lng) * Math.min(Math.abs(end.lng - start.lng), maxDeg)
  return [Math.min(start.lng, lng), Math.min(start.lat, lat), Math.max(start.lng, lng), Math.max(start.lat, lat)]
}

function DrawController({ drawing, maxDeg, onDraft, onDone, onCancel, onInspect }) {
  const start = useRef(null)
  const map = useMapEvents({
    mousedown(e) {
      if (!drawing) return
      start.current = e.latlng
      map.dragging.disable()
    },
    mousemove(e) {
      if (drawing && start.current) onDraft(clampBox(start.current, e.latlng, maxDeg))
    },
    mouseup(e) {
      if (!drawing || !start.current) return
      const box = clampBox(start.current, e.latlng, maxDeg)
      start.current = null
      map.dragging.enable()
      if (box[2] - box[0] > 0.002 && box[3] - box[1] > 0.002) onDone(box)
      else onDraft(null)
    },
    click(e) {
      if (!drawing && onInspect) onInspect(e.latlng.lat, e.latlng.lng)
    },
  })
  useEffect(() => {
    const el = map.getContainer()
    el.style.cursor = drawing ? 'crosshair' : ''
    const key = (ev) => { if (ev.key === 'Escape' && drawing) { start.current = null; map.dragging.enable(); onCancel() } }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [drawing, map, onCancel])
  return null
}

// EMIT coverage: everything WITHOUT data in the dates is dimmed and the covered
// area keeps its imagery, with a white outline; scenes already downloaded get an
// aqua outline (maps in seconds). Canvas, non-interactive: drawing and clicks pass through.
const AREA_STYLE = { color: '#ffffff', weight: 2, opacity: 0.95, fill: false, interactive: false }
const DIM_STYLE = { stroke: false, fillColor: '#0b0b0b', fillOpacity: 0.5, fillRule: 'evenodd', interactive: false }
const STAGED_STYLE = { color: '#1baf7a', weight: 2.5, opacity: 1, fill: false, interactive: false }
const latlngs = (ring) => ring.map(([x, y]) => [y, x])

// The region rectangle with every ring of the covered area cut out (even-odd fill),
// so covered land shows through and gaps inside it are dimmed again.
function dimRings(region, area) {
  const rect = [[region[1], region[0]], [region[1], region[2]], [region[3], region[2]], [region[3], region[0]]]
  const polys = !area ? [] : area.type === 'Polygon' ? [area.coordinates] : area.type === 'MultiPolygon' ? area.coordinates : []
  return [rect, ...polys.flatMap((poly) => poly.map(latlngs))]
}

function CoverageLegend({ coverage, show, setShow }) {
  const n = coverage.features.length
  const staged = coverage.features.filter((f) => f.properties.staged).length
  return (
    <div className="coverage-legend card" aria-label="EMIT coverage">
      <label className="row" style={{ gap: 6, fontWeight: 600 }}>
        <input type="checkbox" checked={show} onChange={(e) => setShow(e.target.checked)} /> EMIT coverage
      </label>
      {n === 0 ? (
        <div className="muted">{coverage.indexed_at ? 'No usable EMIT scene in these dates (whole map dimmed).' : 'Coverage not indexed yet.'}</div>
      ) : (
        <div className="legend">
          <div className="item"><span className="swatch" style={{ background: '#d9d8d2', boxShadow: 'inset 0 0 0 2px #ffffff, 0 0 0 1px var(--axis)' }} />
            Bright, white outline: EMIT data ({n} scenes, {coverage.date_from} to {coverage.date_to}, ≤ {coverage.max_cloud_pct} % cloud)</div>
          <div className="item"><span className="swatch" style={{ background: 'rgba(11,11,11,0.5)' }} />Dimmed: no EMIT scene in these dates</div>
          {staged > 0 && <div className="item"><span className="swatch" style={{ boxShadow: 'inset 0 0 0 2px #1baf7a' }} />
            {staged} already downloaded: maps in seconds</div>}
          <div className="item muted">Agriculture and minerals need a scene covering the whole box</div>
        </div>
      )}
    </div>
  )
}

function FitTo({ bbox }) {
  const map = useMap()
  const done = useRef(null)
  useEffect(() => {
    const key = bbox && bbox.join(',')
    if (bbox && done.current !== key) {
      map.fitBounds(toBounds(bbox), { padding: [40, 40], maxZoom: 14 })
      done.current = key
    }
  }, [bbox, map])
  return null
}

export default function AreaMap({ region, maxDeg, bbox, onBbox, drawing, setDrawing, overlays = [], onInspect,
  marker, coverage, children }) {
  const [draft, setDraft] = useState(null)
  const [base, setBase] = useState('imagery')
  const [showCoverage, setShowCoverage] = useState(true)
  const renderer = useMemo(() => L.canvas({ padding: 0.5 }), [])
  const coverKey = coverage ? `${coverage.date_from}|${coverage.date_to}|${coverage.features.length}` : ''
  const dim = useMemo(() => (coverage ? dimRings(region, coverage.covered_area) : null), [coverage, region])
  const staged = useMemo(() => (coverage
    ? { type: 'FeatureCollection', features: coverage.features.filter((f) => f.properties.staged) } : null), [coverage])
  const center = [(region[1] + region[3]) / 2, (region[0] + region[2]) / 2]
  return (
    <div className="mapwrap" style={{ height: '100%' }}>
      <MapContainer center={center} zoom={7} minZoom={6} maxZoom={17} maxBounds={toBounds(region)}
                    maxBoundsViscosity={0.8} style={{ height: '100%' }} zoomControl>
        <TileLayer key={base} url={BASEMAPS[base].url} attribution={BASEMAPS[base].attribution} maxZoom={19} />
        <Rectangle bounds={toBounds(region)} pathOptions={{ color: '#ffffff', weight: 1, dashArray: '4 6', fill: false }} />
        {dim && showCoverage && <Polygon key={`dim|${coverKey}`} positions={dim} pathOptions={{ ...DIM_STYLE, renderer }} />}
        {coverage && showCoverage && coverage.covered_area && (
          <GeoJSON key={`area|${coverKey}`} data={coverage.covered_area} renderer={renderer} style={() => AREA_STYLE} />
        )}
        {staged && showCoverage && staged.features.length > 0 && (
          <GeoJSON key={`staged|${coverKey}`} data={staged} renderer={renderer} style={() => STAGED_STYLE} />
        )}
        {overlays.map((o) => (
          <ImageOverlay key={o.key} url={o.url} bounds={toBounds(o.bounds)} opacity={o.opacity ?? 0.85}
                        className="pixelated" eventHandlers={{ error: o.onError }} />
        ))}
        {bbox && !draft && <Rectangle bounds={toBounds(bbox)} pathOptions={{ color: '#2a78d6', weight: 2, fill: false }} />}
        {draft && <Rectangle bounds={toBounds(draft)} pathOptions={{ color: '#ffffff', weight: 2, dashArray: '6 4', fillOpacity: 0.1 }} />}
        {marker && <Rectangle bounds={[[marker[0] - 0.0004, marker[1] - 0.0004], [marker[0] + 0.0004, marker[1] + 0.0004]]}
                              pathOptions={{ color: '#ffffff', weight: 2, fill: false }} />}
        <DrawController drawing={drawing} maxDeg={maxDeg} onDraft={setDraft}
                        onDone={(b) => { setDraft(null); setDrawing(false); onBbox(b) }}
                        onCancel={() => { setDraft(null); setDrawing(false) }} onInspect={onInspect} />
        <FitTo bbox={bbox} />
      </MapContainer>
      <div className="map-tools">
        <button className="btn small" onClick={() => setBase(base === 'imagery' ? 'streets' : 'imagery')}>
          {base === 'imagery' ? 'Streets' : 'Imagery'}
        </button>
      </div>
      {coverage && <CoverageLegend coverage={coverage} show={showCoverage} setShow={setShowCoverage} />}
      {drawing && <div className="map-hint">Drag a box (up to {maxDeg}° per side). Esc cancels.</div>}
      {children}
    </div>
  )
}
