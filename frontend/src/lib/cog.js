// Reading the result COGs directly in the browser (HTTP range requests against
// the presigned URL): pixel inspection and client-side re-colouring, with no
// extra backend endpoint and no load on the gateway.
import { fromUrl } from 'geotiff'

const cache = new Map()   // tif_url (minus signature) -> Promise<{image, bbox, width, height}>

function cacheKey(url) {
  return url.split('?')[0]
}

async function open(url) {
  const key = cacheKey(url)
  if (!cache.has(key)) {
    const p = (async () => {
      const tiff = await fromUrl(url, { allowFullFile: false })
      const image = await tiff.getImage()
      return { image, bbox: image.getBoundingBox(), width: image.getWidth(), height: image.getHeight(),
        nodata: image.getGDALNoData() }
    })()
    p.catch(() => cache.delete(key))
    cache.set(key, p)
  }
  return cache.get(key)
}

export function forgetCogs() {
  cache.clear()
}

// Value of a single-band COG at lon/lat, or null outside / no data.
export async function valueAt(url, lon, lat) {
  const { image, bbox, width, height, nodata } = await open(url)
  const [x0, y0, x1, y1] = bbox
  if (lon < x0 || lon > x1 || lat < y0 || lat > y1) return null
  const col = Math.min(width - 1, Math.floor(((lon - x0) / (x1 - x0)) * width))
  const row = Math.min(height - 1, Math.floor(((y1 - lat) / (y1 - y0)) * height))
  const [band] = await image.readRasters({ window: [col, row, col + 1, row + 1] })
  const v = band[0]
  if (v === undefined || Number.isNaN(v) || (nodata !== null && v === nodata)) return null
  return v
}

// Render a categorical COG to a data URL, emphasising one class.
export async function renderCategorical(url, palette, highlight = null) {
  const { image, width, height, nodata } = await open(url)
  const [band] = await image.readRasters()
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const ctx = canvas.getContext('2d')
  const img = ctx.createImageData(width, height)
  const rgba = palette.map((hex) => {
    const h = hex.replace('#', '')
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16),
      h.length === 8 ? parseInt(h.slice(6, 8), 16) : 255]
  })
  for (let i = 0; i < band.length; i++) {
    const c = band[i]
    if (c === nodata || c === 255 || c >= rgba.length) continue
    const [r, g, b, a] = rgba[c]
    const o = i * 4
    if (highlight === null || c === highlight) {
      img.data[o] = r; img.data[o + 1] = g; img.data[o + 2] = b; img.data[o + 3] = a
    } else {     // other classes recede to a faint neutral so the chosen one stands out
      img.data[o] = 137; img.data[o + 1] = 135; img.data[o + 2] = 129; img.data[o + 3] = 70
    }
  }
  ctx.putImageData(img, 0, 0)
  return canvas.toDataURL('image/png')
}
