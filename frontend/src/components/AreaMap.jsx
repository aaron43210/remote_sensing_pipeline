import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import L from 'leaflet'
import { GeoJSON, ImageOverlay, MapContainer, Polygon, Rectangle, TileLayer, useMap, useMapEvents } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import { clampBox, finishBox } from '../lib/draw'
import { COARSE, useMediaQuery } from '../lib/useMediaQuery'

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

// Pointer Events: one path for mouse, pen and finger. While drawing, the map
// cannot pan or box-zoom and the browser does not scroll (touch-action: none),
// so a one-finger drag draws instead of panning.
function DrawController({ drawing, maxDeg, onDraft, onDone, onCancel, onInspect }) {
  const map = useMapEvents({
    click(e) {
      if (!drawing && onInspect) onInspect(e.latlng.lat, e.latlng.lng)
    },
  })
  useEffect(() => {
    if (!drawing) return undefined
    const el = map.getContainer()
    let start = null
    map.dragging.disable()
    map.boxZoom.disable()
    el.style.touchAction = 'none'
    el.style.cursor = 'crosshair'
    const at = (ev) => map.mouseEventToLatLng(ev)
    const down = (ev) => {
      if (ev.button > 0) return                     // left button, pen or finger only
      start = at(ev)
      el.setPointerCapture?.(ev.pointerId)
      ev.preventDefault()
    }
    const move = (ev) => { if (start) onDraft(clampBox(start, at(ev), maxDeg)) }
    const up = (ev) => {
      if (!start) return
      const box = finishBox(start, at(ev), maxDeg)
      start = null
      if (box) onDone(box)
      else onDraft(null)
    }
    const cancel = () => { start = null; onDraft(null) }
    const key = (ev) => { if (ev.key === 'Escape') { start = null; onCancel() } }
    el.addEventListener('pointerdown', down)
    el.addEventListener('pointermove', move)
    el.addEventListener('pointerup', up)
    el.addEventListener('pointercancel', cancel)
    window.addEventListener('keydown', key)
    return () => {
      el.removeEventListener('pointerdown', down)
      el.removeEventListener('pointermove', move)
      el.removeEventListener('pointerup', up)
      el.removeEventListener('pointercancel', cancel)
      window.removeEventListener('keydown', key)
      map.dragging.enable()
      map.boxZoom.enable()
      el.style.touchAction = ''
      el.style.cursor = ''
    }
  }, [drawing, map, maxDeg, onDraft, onDone, onCancel])
  return null
}

// Leaflet follows window resizes only. Its box also changes when the panel or the
// phone sheet moves, or the layout settles: refit then too, or tiles stay grey.
function AutoResize() {
  const map = useMap()
  useEffect(() => {
    const box = map.getContainer().parentElement
    if (!box || typeof ResizeObserver === 'undefined') return undefined
    let frame = 0
    const ro = new ResizeObserver(() => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => map.invalidateSize({ pan: false }))
    })
    ro.observe(box)
    return () => { cancelAnimationFrame(frame); ro.disconnect() }
  }, [map])
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
  const touch = useMediaQuery(COARSE)
  const stopDrawing = useCallback(() => { setDraft(null); setDrawing(false) }, [setDrawing])
  const finishDrawing = useCallback((b) => { setDraft(null); setDrawing(false); onBbox(b) }, [setDrawing, onBbox])
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
        <DrawController drawing={drawing} maxDeg={maxDeg} onDraft={setDraft} onDone={finishDrawing}
                        onCancel={stopDrawing} onInspect={onInspect} />
        <FitTo bbox={bbox} />
        <AutoResize />
      </MapContainer>
      <div className="map-tools">
        {drawing && <button className="btn small" onClick={stopDrawing}>Cancel</button>}
        <button className="btn small" onClick={() => setBase(base === 'imagery' ? 'streets' : 'imagery')}>
          {base === 'imagery' ? 'Streets' : 'Imagery'}
        </button>
      </div>
      {coverage && <CoverageLegend coverage={coverage} show={showCoverage} setShow={setShowCoverage} />}
      {drawing && <div className="map-hint">{touch
        ? `Drag with one finger to draw a box (up to ${maxDeg}° per side).`
        : `Drag a box (up to ${maxDeg}° per side). Esc cancels.`}</div>}
      {children}
    </div>
  )
}
