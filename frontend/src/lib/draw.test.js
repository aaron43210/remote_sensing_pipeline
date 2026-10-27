import { describe, expect, it } from 'vitest'
import { clampBox, finishBox } from './draw'

const close = (got, want) => want.forEach((v, i) => expect(got[i]).toBeCloseTo(v, 9))

describe('drawing a box', () => {
  it('clamps each side to maxDeg in the drag direction', () => {
    close(clampBox({ lat: 36, lng: -120 }, { lat: 35, lng: -119 }, 0.1), [-120, 35.9, -119.9, 36])
  })
  it('works whichever corner the drag starts from', () => {
    close(clampBox({ lat: 36.05, lng: -119.95 }, { lat: 36, lng: -120 }, 0.1), [-120, 36, -119.95, 36.05])
  })
  it('ignores a tap (box too small)', () => {
    expect(finishBox({ lat: 36, lng: -120 }, { lat: 36.001, lng: -119.999 }, 0.1)).toBeNull()
  })
  it('returns the box for a real drag', () => {
    close(finishBox({ lat: 36, lng: -120 }, { lat: 36.05, lng: -119.95 }, 0.1), [-120, 36, -119.95, 36.05])
  })
})
