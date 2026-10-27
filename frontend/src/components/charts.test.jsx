import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { describe, expect, it } from 'vitest'
import { Histogram } from './charts'

globalThis.IS_REACT_ACT_ENVIRONMENT = true

const press = (el) => act(() => { el.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true })) })

describe('Histogram on touch', () => {
  it('tap shows the bar tooltip, tapping elsewhere hides it', () => {
    const el = document.createElement('div')
    document.body.appendChild(el)
    const root = createRoot(el)
    act(() => root.render(<Histogram histogram={{ edges: [0, 1, 2], counts: [5, 7] }} units="m2/m2" />))
    expect(el.querySelector('.tooltip')).toBeNull()
    press(el.querySelectorAll('svg g')[el.querySelectorAll('svg g').length - 1])   // the second bar
    expect(el.querySelector('.tooltip').textContent).toContain('7')
    press(document.body)
    expect(el.querySelector('.tooltip')).toBeNull()
    act(() => root.unmount())
    el.remove()
  })
})
