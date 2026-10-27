import { useCallback, useSyncExternalStore } from 'react'

// The same bands as styles.css: keep them in step.
export const PHONE = '(max-width: 640px)'
export const LANDSCAPE_PHONE = '(max-height: 500px) and (orientation: landscape)'
export const COARSE = '(pointer: coarse)'

const media = (query) => (typeof window !== 'undefined' && window.matchMedia ? window.matchMedia(query) : null)

export function useMediaQuery(query) {
  const subscribe = useCallback((notify) => {
    const mql = media(query)
    if (!mql) return () => {}
    mql.addEventListener('change', notify)
    return () => mql.removeEventListener('change', notify)
  }, [query])
  return useSyncExternalStore(subscribe, () => media(query)?.matches ?? false, () => false)
}
