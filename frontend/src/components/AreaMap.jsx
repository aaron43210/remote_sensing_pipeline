import { useEffect, useRef, useState } from 'react'
import { ImageOverlay, MapContainer, Rectangle, TileLayer, useMap, useMapEvents } from 'react-leaflet'
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
  marker, children }) {
  const [draft, setDraft] = useState(null)
  const [base, setBase] = useState('imagery')
  const center = [(region[1] + region[3]) / 2, (region[0] + region[2]) / 2]
  return (
    <div className="mapwrap" style={{ height: '100%' }}>
      <MapContainer center={center} zoom={7} minZoom={6} maxZoom={17} maxBounds={toBounds(region)}
                    maxBoundsViscosity={0.8} style={{ height: '100%' }} zoomControl>
        <TileLayer key={base} url={BASEMAPS[base].url} attribution={BASEMAPS[base].attribution} maxZoom={19} />
        <Rectangle bounds={toBounds(region)} pathOptions={{ color: '#ffffff', weight: 1, dashArray: '4 6', fill: false }} />
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
      {drawing && <div className="map-hint">Drag a box (up to {maxDeg}° per side). Esc cancels.</div>}
      {children}
    </div>
  )
}
