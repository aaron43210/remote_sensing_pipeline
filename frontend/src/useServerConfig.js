import { useEffect, useState } from 'react'
import { api } from './api'

let cached = null

// Server-side limits (region, bbox size, fixtures). Never duplicated in the UI.
export function useServerConfig() {
  const [config, setConfig] = useState(cached)
  useEffect(() => {
    if (cached) return
    api.config().then((c) => { cached = c; setConfig(c) }).catch(() => {
      // keep the UI usable if /config is briefly unavailable; the server still enforces its limits
      setConfig({ allowed_region: [-124.5, 32.5, -114, 42], max_bbox_deg: 0.1, allow_fixtures: false,
        tasks: ['agriculture', 'mineral', 'thermal'] })
    })
  }, [])
  return config
}
