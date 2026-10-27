// Small, dependency-free SVG charts that follow the data-viz mark specs:
// thin bars with 4px rounded data-ends anchored at the baseline, a 2px surface
// gap between touching bars, hairline recessive grid, a hover tooltip on every
// mark, and a table view so nothing depends on colour alone.
import { useCallback, useEffect, useRef, useState } from 'react'
import { compact, fmt, pct } from '../lib/format'

function roundedTopBar(x, y, w, h, r = 4) {
  if (h <= 0) return ''
  const rr = Math.min(r, w / 2, h)
  return `M${x},${y + h} V${y + rr} Q${x},${y} ${x + rr},${y} H${x + w - rr} Q${x + w},${y} ${x + w},${y + rr} V${y + h} Z`
}

function roundedEndBar(x, y, w, h, r = 4) {
  if (w <= 0) return ''
  const rr = Math.min(r, h / 2, w)
  return `M${x},${y} H${x + w - rr} Q${x + w},${y} ${x + w},${y + rr} V${y + h - rr} Q${x + w},${y + h} ${x + w - rr},${y + h} H${x} Z`
}

function niceMax(v) {
  if (v <= 0) return 1
  const p = 10 ** Math.floor(Math.log10(v))
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p
  return 10 * p
}

// Hover with a mouse, tap with a finger: either sets `hover`; a tap outside clears it.
function useTapAway(ref, active, clear) {
  useEffect(() => {
    if (!active) return undefined
    const away = (e) => { if (!ref.current?.contains(e.target)) clear() }
    document.addEventListener('pointerdown', away)
    return () => document.removeEventListener('pointerdown', away)
  }, [ref, active, clear])
}

// Histogram of one map layer (single series: the title names it, no legend box).
export function Histogram({ histogram, units, color = 'var(--series-1)', height = 140 }) {
  const [hover, setHover] = useState(null)
  const [table, setTable] = useState(false)
  const ref = useRef(null)
  const clear = useCallback(() => setHover(null), [])
  useTapAway(ref, hover !== null, clear)
  if (!histogram || !histogram.counts?.length) return <p className="muted">No distribution for this layer.</p>
  const { counts, edges } = histogram
  const W = 320, H = height, padL = 34, padB = 22, padT = 8
  const max = niceMax(Math.max(...counts))
  const plotW = W - padL - 4, plotH = H - padB - padT
  const slot = plotW / counts.length
  const barW = Math.min(24, slot - 2)
  const y = (v) => padT + plotH - (v / max) * plotH
  const ticks = [0, max / 2, max]
  return (
    <div className="chart" ref={ref}>
      <div className="row between" style={{ marginBottom: 4 }}>
        <span className="muted" style={{ fontSize: 12 }}>Pixels per value{units ? ` (${units})` : ''}</span>
        <button className="btn small" onClick={() => setTable(!table)} aria-pressed={table}>{table ? 'Chart' : 'Table'}</button>
      </div>
      {table ? (
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>From</th><th>To</th><th className="r">Pixels</th></tr></thead>
            <tbody>{counts.map((c, i) => (
              <tr key={i}><td className="num">{fmt(edges[i])}</td><td className="num">{fmt(edges[i + 1])}</td><td className="r">{c}</td></tr>
            ))}</tbody>
          </table>
        </div>
      ) : (
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Histogram${units ? ` in ${units}` : ''}`}
             onPointerLeave={(e) => { if (e.pointerType !== 'touch') setHover(null) }}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={padL} x2={W - 4} y1={y(t)} y2={y(t)} stroke="var(--grid)" strokeWidth="1" />
              <text x={padL - 6} y={y(t) + 3} textAnchor="end">{compact(t)}</text>
            </g>
          ))}
          {counts.map((c, i) => {
            const x = padL + i * slot + (slot - barW) / 2
            return (
              <g key={i} onPointerEnter={() => setHover(i)} onPointerDown={() => setHover(i)}>
                <rect x={padL + i * slot} y={padT} width={slot} height={plotH} fill="transparent" />
                <path d={roundedTopBar(x, y(c), barW, padT + plotH - y(c))} fill={color}
                      opacity={hover === null || hover === i ? 1 : 0.45} />
              </g>
            )
          })}
          <line x1={padL} x2={W - 4} y1={padT + plotH} y2={padT + plotH} stroke="var(--axis)" strokeWidth="1" />
          <text x={padL} y={H - 6} textAnchor="start">{fmt(edges[0])}</text>
          <text x={W - 4} y={H - 6} textAnchor="end">{fmt(edges[edges.length - 1])}</text>
        </svg>
      )}
      {!table && hover !== null && (
        <div className="tooltip" style={{ left: `${((padL + (hover + 0.5) * slot) / W) * 100}%`, top: 24 }}>
          <strong>{counts[hover]}</strong> pixels · {fmt(edges[hover])}–{fmt(edges[hover + 1])}{units ? ` ${units}` : ''}
        </div>
      )}
    </div>
  )
}

// Horizontal bars of class fractions. Identity: the label text (ink) and a
// swatch beside it; the bar colour repeats the map colour of that class.
export function CompositionBars({ rows, onPick, picked }) {
  const [hover, setHover] = useState(null)
  const [table, setTable] = useState(false)
  const shown = rows.filter((r) => r.fraction > 0)
  if (!shown.length) return <p className="muted">Nothing mapped in this area.</p>
  const max = Math.max(...shown.map((r) => r.fraction))
  return (
    <div className="chart">
      <div className="row between" style={{ marginBottom: 6 }}>
        <span className="muted" style={{ fontSize: 12 }}>Share of mapped pixels</span>
        <button className="btn small" onClick={() => setTable(!table)} aria-pressed={table}>{table ? 'Chart' : 'Table'}</button>
      </div>
      {table ? (
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Class</th><th>Group</th><th className="r">Share</th><th className="r">Pixels</th></tr></thead>
            <tbody>{shown.map((r) => (
              <tr key={r.name}><td>{r.name}</td><td className="secondary">{r.group || '–'}</td>
                <td className="r">{pct(r.fraction)}</td><td className="r">{r.pixels ?? '–'}</td></tr>
            ))}</tbody>
          </table>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }} onPointerLeave={() => setHover(null)}>
          {shown.map((r) => (
            <button key={r.name} type="button" className="bar-row" onPointerEnter={() => setHover(r.name)}
                    onClick={() => onPick && onPick(picked === r.name ? null : r.name)}
                    title={`${r.name}: ${pct(r.fraction)}`}
                    style={{ display: 'grid', gridTemplateColumns: '120px 1fr 56px', alignItems: 'center', gap: 8,
                      background: picked === r.name ? 'var(--surface-2)' : 'transparent', border: 0, padding: '3px 4px',
                      borderRadius: 6, cursor: onPick ? 'pointer' : 'default', color: 'var(--text-primary)', textAlign: 'left' }}>
              <span className="row" style={{ gap: 6, minWidth: 0 }}>
                <span className="swatch" style={{ background: r.color }} />
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.name}</span>
              </span>
              <svg viewBox="0 0 200 14" preserveAspectRatio="none" style={{ height: 14 }} aria-hidden="true">
                <path d={roundedEndBar(0, 1, Math.max(2, (r.fraction / max) * 200), 12)} fill={r.color}
                      opacity={hover === null || hover === r.name ? 1 : 0.5} />
              </svg>
              <span className="num secondary" style={{ textAlign: 'right' }}>{pct(r.fraction)}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// Legend for a map layer, built only from its metadata.
export function Legend({ layer }) {
  if (!layer) return null
  if (layer.kind === 'categorical') {
    const pal = layer.stats?.palette || []
    const frac = layer.stats?.class_fraction || {}
    const groups = layer.stats?.groups
    if (layer.name === 'mineral_class' && groups) {   // colour = spectral group; list members under it
      const present = (layer.classes || []).map((c, i) => ({ c, i })).filter(({ c }) => frac[c])
      const byGroup = present.reduce((acc, x) => ({ ...acc, [groups[x.c]]: [...(acc[groups[x.c]] || []), x] }), {})
      return (
        <div className="legend">
          {Object.entries(byGroup).map(([g, members]) => (
            <div key={g} style={{ marginBottom: 4 }}>
              <div className="item"><span className="swatch" style={{ background: pal[members[0].i] }} />
                <strong style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{g}</strong></div>
              {members.map(({ c }) => (
                <div className="item" key={c} style={{ paddingLeft: 20 }}>
                  <span style={{ flex: 1 }}>{c}</span><span className="num muted">{pct(frac[c])}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      )
    }
    return (
      <div className="legend">
        {(layer.classes || []).map((c, i) => {
          const colour = pal[i]
          if (!colour || colour === '#00000000') return null
          if (layer.name === 'mineral_class' && !frac[c]) return null   // classes present in this area only
          return (
            <div className="item" key={c}>
              <span className="swatch" style={{ background: colour }} />
              <span style={{ flex: 1 }}>{c}</span>
              {frac[c] !== undefined && <span className="num muted">{pct(frac[c])}</span>}
            </div>
          )
        })}
      </div>
    )
  }
  const ramp = layer.stats?.ramp || []
  return (
    <div>
      <div className="ramp" style={{ background: `linear-gradient(90deg, ${ramp.join(', ')})` }} />
      <div className="ramp-labels num">
        <span>{fmt(layer.vmin)}</span>
        <span>{layer.units}</span>
        <span>{fmt(layer.vmax)}</span>
      </div>
    </div>
  )
}

export function StatTile({ label, value, sub }) {
  return (
    <div className="tile">
      <span className="label">{label}</span>
      <span className="value">{value}</span>
      {sub && <span className="sub">{sub}</span>}
    </div>
  )
}
