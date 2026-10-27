import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { COARSE, LANDSCAPE_PHONE, PHONE, useMediaQuery } from './useMediaQuery'

globalThis.IS_REACT_ACT_ENVIRONMENT = true

function stubMatchMedia(initial) {
  const listeners = new Set()
  const mql = {
    matches: initial,
    addEventListener: vi.fn((_, fn) => listeners.add(fn)),
    removeEventListener: vi.fn((_, fn) => listeners.delete(fn)),
  }
  window.matchMedia = vi.fn(() => mql)
  return { mql, fire(matches) { mql.matches = matches; listeners.forEach((fn) => fn({ matches })) } }
}

function render(query) {
  const el = document.createElement('div')
  const root = createRoot(el)
  const Probe = () => String(useMediaQuery(query))
  act(() => root.render(createElement(Probe)))
  return { el, root }
}

const realMatchMedia = window.matchMedia
afterEach(() => { window.matchMedia = realMatchMedia })

describe('useMediaQuery', () => {
  it('exports the shared breakpoints', () => {
    expect(PHONE).toBe('(max-width: 640px)')
    expect(LANDSCAPE_PHONE).toBe('(max-height: 500px) and (orientation: landscape) and (pointer: coarse)')
    expect(COARSE).toBe('(pointer: coarse)')
  })
  it('follows the media query and its changes', () => {
    const mm = stubMatchMedia(false)
    const { el, root } = render(PHONE)
    expect(el.textContent).toBe('false')
    act(() => mm.fire(true))
    expect(el.textContent).toBe('true')
    act(() => root.unmount())
  })
  it('is false when matchMedia does not exist', () => {
    delete window.matchMedia
    const { el, root } = render(PHONE)
    expect(el.textContent).toBe('false')
    act(() => root.unmount())
  })
  it('removes its listener on unmount', () => {
    const mm = stubMatchMedia(true)
    const { root } = render(COARSE)
    const added = mm.mql.addEventListener.mock.calls[0][1]
    act(() => root.unmount())
    expect(mm.mql.removeEventListener).toHaveBeenCalledWith('change', added)
  })
})
