import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { valueAt } from '../lib/cog'
import { fmt } from '../lib/format'
import { layerLabel, TASKS } from '../lib/layers'

const KEY_LAYERS = new Set(['landcover', 'ndvi', 'agri_Cab', 'agri_Car', 'agri_Cw', 'agri_Cm', 'agri_LAI', 'agri_ALA',
  'mineral_class', 'mineral_confidence', 'mineral_quality', 'thermal_lst', 'thermal_global_class',
  'thermal_uhi_class', 'thermal_hotspots', 'thermal_local_anomaly'])

// Values of every map at one clicked point, read straight from the COGs.
export default function PixelInspector({ point, layers, onClose }) {
  const [rows, setRows] = useState(null)
  useEffect(() => {
    if (!point) return undefined
    let live = true
    setRows(null)
    const wanted = layers.filter((l) => KEY_LAYERS.has(l.name))
    Promise.all(wanted.map(async (l) => {
      try {
        const v = await valueAt(l.tif_url, point[1], point[0])
        let text = '–'
        if (v !== null) {
          text = l.kind === 'categorical' ? (l.classes?.[v] ?? `class ${v}`) : `${fmt(v)} ${l.units}`
        }
        return { task: l.task, name: l.name, text }
      } catch {
        return { task: l.task, name: l.name, text: 'unavailable' }
      }
    })).then((r) => live && setRows(r))
    return () => { live = false }
  }, [point, layers])
  if (!point) return null
  const byTask = (rows || []).reduce((acc, r) => ({ ...acc, [r.task]: [...(acc[r.task] || []), r] }), {})
  return (
    <div className="card inspector" role="dialog" aria-label="Pixel values">
      <div className="row between">
        <h2 style={{ margin: 0 }}>Pixel</h2>
        <button className="btn small" onClick={onClose} aria-label="Close"><X size={12} /></button>
      </div>
      <p className="muted num" style={{ fontSize: 12 }}>{point[0].toFixed(5)}°, {point[1].toFixed(5)}°</p>
      {!rows && <p className="muted">Reading values…</p>}
      {Object.entries(byTask).map(([task, rs]) => (
        <div key={task} style={{ marginBottom: 8 }}>
          <h3>{TASKS[task]?.label || task}</h3>
          <table className="data"><tbody>
            {rs.map((r) => <tr key={r.name}><td>{layerLabel(r.name)}</td><td className="r">{r.text}</td></tr>)}
          </tbody></table>
        </div>
      ))}
    </div>
  )
}
