// Number and label formatting shared by every view.

export function fmt(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '–'
  const a = Math.abs(v)
  if (a !== 0 && (a < 0.01 || a >= 1e5)) return v.toExponential(1)
  if (a >= 1000) return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(v)
  return v.toFixed(a >= 100 ? 0 : a >= 10 ? Math.min(1, digits) : digits)
}

export function compact(v) {
  if (v === null || v === undefined) return '–'
  return new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(v)
}

export const pct = (v, digits = 1) => (v === null || v === undefined ? '–' : `${(v * 100).toFixed(digits)} %`)

export function duration(seconds) {
  if (seconds === null || seconds === undefined) return '–'
  if (seconds < 60) return `${Math.round(seconds)} s`
  const m = Math.floor(seconds / 60)
  return m < 60 ? `${m} min ${Math.round(seconds % 60)} s` : `${Math.floor(m / 60)} h ${m % 60} min`
}

export function when(iso) {
  if (!iso) return '–'
  const d = new Date(iso)
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export function dateOnly(iso) {
  if (!iso) return '–'
  return new Date(iso).toLocaleDateString(undefined, { dateStyle: 'medium' })
}

export const titleCase = (s) => s.charAt(0).toUpperCase() + s.slice(1)

export function bboxLabel(b) {
  if (!b) return ''
  return `${b[1].toFixed(3)}°, ${b[0].toFixed(3)}° → ${b[3].toFixed(3)}°, ${b[2].toFixed(3)}°`
}

// approximate area of a lon/lat box in km²
export function bboxAreaKm2(b) {
  const lat = ((b[1] + b[3]) / 2) * (Math.PI / 180)
  return (b[2] - b[0]) * 111.32 * Math.cos(lat) * (b[3] - b[1]) * 110.57
}

export const todayIso = () => new Date().toISOString().slice(0, 10)
export const yearAgoIso = () => new Date(Date.now() - 365 * 864e5).toISOString().slice(0, 10)
