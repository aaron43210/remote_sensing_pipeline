// Box drawing math, independent of the input device.

export function clampBox(start, end, maxDeg) {
  const lat = start.lat + Math.sign(end.lat - start.lat) * Math.min(Math.abs(end.lat - start.lat), maxDeg)
  const lng = start.lng + Math.sign(end.lng - start.lng) * Math.min(Math.abs(end.lng - start.lng), maxDeg)
  return [Math.min(start.lng, lng), Math.min(start.lat, lat), Math.max(start.lng, lng), Math.max(start.lat, lat)]
}

// The finished box, or null for a tap or a sliver (either side not larger than minDeg).
export function finishBox(start, end, maxDeg, minDeg = 0.002) {
  const box = clampBox(start, end, maxDeg)
  return box[2] - box[0] > minDeg && box[3] - box[1] > minDeg ? box : null
}
