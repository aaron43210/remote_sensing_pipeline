// What each map layer is called and how it is grouped in the UI. Colours,
// units, ranges and classes always come from the layer metadata the backend
// sends (single source of truth); this file only adds human labels.

export const TASKS = {
  agriculture: { label: 'Agriculture', blurb: 'Leaf chlorophyll, water and canopy structure (PROSAIL inversion)' },
  mineral: { label: 'Minerals', blurb: 'Dominant mineral, abundances and confidence (USGS library, SAM + SMA)' },
  thermal: { label: 'Thermal', blurb: 'Land surface temperature, heat zones and hotspots (Landsat 8/9)' },
}

const NAMES = {
  landcover: 'Land cover',
  ndvi: 'NDVI (vegetation index)',
  agri_Cab: 'Leaf chlorophyll (Cab)',
  agri_Car: 'Leaf carotenoids (Car)',
  agri_Cw: 'Leaf water (Cw)',
  agri_Cm: 'Leaf dry matter (Cm)',
  agri_LAI: 'Leaf area index (LAI)',
  agri_ALA: 'Mean leaf angle (ALA)',
  mineral_class: 'Dominant mineral',
  mineral_abundance: 'Abundances (all minerals)',
  mineral_confidence: 'Classification confidence',
  mineral_quality: 'Quality (SAM + SMA)',
  thermal_lst: 'Land surface temperature',
  thermal_global_class: 'Temperature anomaly (scene)',
  thermal_uhi_class: 'Heat zones (urban / rural)',
  thermal_hotspots: 'Hotspots',
  thermal_local_anomaly: 'Local anomalies (11×11)',
  thermal_real_scene_min: 'Worst real Landsat scene (every thermal map)',
}

export function layerLabel(name) {
  if (NAMES[name]) return NAMES[name]
  if (name.endsWith('_std')) return `Uncertainty of ${layerLabel(name.slice(0, -4)).replace(/ \(.*\)/, '')}`
  if (name.startsWith('mineral_abundance_')) return `Abundance: ${name.slice(18)}`
  return name
}

// The few maps most people need, per task (shown first, under "Main maps");
// everything else (other traits, uncertainties, abundances, diagnostics) is "More detail".
export const MAIN_LAYERS = {
  agriculture: ['agri_LAI', 'agri_Cab', 'agri_Cw', 'ndvi'],
  mineral: ['mineral_class'],
  thermal: ['thermal_lst', 'thermal_uhi_class', 'thermal_hotspots'],
}

export const isMainLayer = (name, task) => (MAIN_LAYERS[task] || []).includes(name)

// Main layers first; then other traits, uncertainty and per-mineral abundance.
export function orderLayers(layers, task) {
  const primary = {
    agriculture: [...MAIN_LAYERS.agriculture, 'agri_Cm', 'agri_Car', 'agri_ALA', 'landcover'],
    mineral: [...MAIN_LAYERS.mineral, 'mineral_confidence', 'mineral_quality', 'landcover'],
    thermal: [...MAIN_LAYERS.thermal, 'thermal_global_class', 'thermal_local_anomaly'],
  }[task] || []
  const rank = (n) => {
    const i = primary.indexOf(n)
    if (i >= 0) return i
    if (n.endsWith('_std')) return 100
    return 200
  }
  return layers.filter((l) => l.task === task).sort((a, b) => rank(a.name) - rank(b.name) || a.name.localeCompare(b.name))
}

export const isFinal = (status) => ['COMPLETED', 'PARTIAL', 'FAILED', 'NO_DATA', 'CANCELLED'].includes(status)

export const STATUS_TONE = {
  COMPLETED: 'good', PARTIAL: 'warning', FAILED: 'critical', NO_DATA: 'warning', CANCELLED: '',
  RUNNING: '', QUEUED: '',
}
