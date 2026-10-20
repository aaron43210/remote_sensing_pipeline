import { describe, expect, it } from 'vitest'
import { bboxAreaKm2, duration, fmt, pct } from './format'
import { isFinal, layerLabel, orderLayers } from './layers'

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
  it('knows the final job states', () => {
    expect(isFinal('COMPLETED')).toBe(true)
    expect(isFinal('RUNNING')).toBe(false)
  })
})
