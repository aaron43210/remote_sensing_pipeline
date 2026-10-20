// EMIT coverage helpers: which acquisitions fully cover a box (the same rule the
// ingestion service uses to pick a scene), computed on the GeoJSON from /api/coverage.

function inRing(lon, lat, ring) {
  let inside = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i]
    const [xj, yj] = ring[j]
    if ((yi > lat) !== (yj > lat) && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside
  }
  return inside
}

function polygons(geometry) {
  if (!geometry) return []
  if (geometry.type === 'Polygon') return [geometry.coordinates]
  if (geometry.type === 'MultiPolygon') return geometry.coordinates
  return []
}

// True when one polygon of the footprint contains all four corners and the centre
// of the box (EMIT footprints are convex quadrilaterals, so that is containment).
export function coversBox(geometry, b) {
  const pts = [[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]]
  return polygons(geometry).some((poly) => pts.every(([x, y]) => inRing(x, y, poly[0]) && !poly.slice(1).some((h) => inRing(x, y, h))))
}

// Scenes covering the box, clearest first then newest (the order ingestion picks in).
export function scenesCovering(coverage, bbox) {
  if (!coverage || !bbox) return []
  return coverage.features
    .filter((f) => coversBox(f.geometry, bbox))
    .map((f) => f.properties)
    .sort((a, b) => Math.round((a.cloud_pct ?? 0) / 10) - Math.round((b.cloud_pct ?? 0) / 10)
      || b.acquired_at.localeCompare(a.acquired_at))
}
