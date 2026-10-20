import { describe, expect, it } from 'vitest'
import { coversBox, scenesCovering } from './coverage'
import { bboxAreaKm2, duration, fmt, pct } from './format'
import { isFinal, isMainLayer, layerLabel, orderLayers } from './layers'

describe('format', () => {
  it('formats numbers for maps and tiles', () => {
    expect(fmt(null)).toBe('–')
    expect(fmt(3.14159)).toBe('3.14')
    expect(fmt(42.5)).toBe('42.5')
    expect(fmt(0.004)).toBe('4.0e-3')
    expect(pct(0.9985)).toBe('99.9 %')
    expect(duration(75)).toBe('1 min 15 s')
  })
  it('computes a bbox area close to the true value', () => {
    // 0.1° x 0.1° at 36.7° N is about 8.9 km x 11.1 km
    expect(bboxAreaKm2([-119.9, 36.7, -119.8, 36.8])).toBeGreaterThan(95)
    expect(bboxAreaKm2([-119.9, 36.7, -119.8, 36.8])).toBeLessThan(102)
  })
})

describe('layers', () => {
  it('labels layers for people', () => {
    expect(layerLabel('agri_LAI')).toBe('Leaf area index (LAI)')
    expect(layerLabel('agri_LAI_std')).toBe('Uncertainty of Leaf area index')
    expect(layerLabel('mineral_abundance_kaolinite')).toBe('Abundance: kaolinite')
  })
  it('orders primary layers first within a task', () => {
    const ls = ['agri_Cab_std', 'ndvi', 'agri_Cab', 'agri_LAI', 'mineral_class'].map((name) => ({
      name, task: name.startsWith('mineral') ? 'mineral' : 'agriculture' }))
    expect(orderLayers(ls, 'agriculture').map((l) => l.name)).toEqual(['agri_LAI', 'agri_Cab', 'ndvi', 'agri_Cab_std'])
  })
  it('separates main maps from detail layers', () => {
    expect(isMainLayer('mineral_class', 'mineral')).toBe(true)
    expect(isMainLayer('mineral_abundance_quartz', 'mineral')).toBe(false)
    expect(isMainLayer('agri_LAI', 'agriculture')).toBe(true)
    expect(isMainLayer('agri_LAI_std', 'agriculture')).toBe(false)
  })
  it('knows the final job states', () => {
    expect(isFinal('COMPLETED')).toBe(true)
    expect(isFinal('RUNNING')).toBe(false)
  })
})

describe('coverage', () => {
  // an EMIT-like footprint: a tilted quadrilateral
  const quad = { type: 'Polygon', coordinates: [[[-120.5, 36.0], [-119.6, 36.2], [-119.8, 37.0], [-120.7, 36.8], [-120.5, 36.0]]] }
  it('says whether a footprint fully covers a box', () => {
    expect(coversBox(quad, [-120.1, 36.6, -120.05, 36.65])).toBe(true)
    expect(coversBox(quad, [-119.7, 36.6, -119.6, 36.7])).toBe(false)     // crosses the east edge
    expect(coversBox({ type: 'MultiPolygon', coordinates: [quad.coordinates] }, [-120.1, 36.6, -120.05, 36.65])).toBe(true)
  })
  it('orders covering scenes clearest first, then newest', () => {
    const fc = { features: [
      { geometry: quad, properties: { granule_id: 'a', acquired_at: '2024-07-01', cloud_pct: 30 } },
      { geometry: quad, properties: { granule_id: 'b', acquired_at: '2023-08-22', cloud_pct: 1 } },
      { geometry: quad, properties: { granule_id: 'c', acquired_at: '2024-09-01', cloud_pct: 2 } },
    ] }
    expect(scenesCovering(fc, [-120.1, 36.6, -120.05, 36.65]).map((p) => p.granule_id)).toEqual(['c', 'b', 'a'])
    expect(scenesCovering(fc, [-118, 35, -117.9, 35.1])).toEqual([])
  })
})
