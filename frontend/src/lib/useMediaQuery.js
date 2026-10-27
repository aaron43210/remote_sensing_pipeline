import { useCallback, useSyncExternalStore } from 'react'

// The same bands as styles.css: keep them in step.
export const PHONE = '(max-width: 640px)'
export const LANDSCAPE_PHONE = '(max-height: 500px) and (orientation: landscape) and (pointer: coarse)'   // not a short desktop window
export const COARSE = '(pointer: coarse)'

// One MediaQueryList per query (per matchMedia implementation), reused by every render:
// getSnapshot runs on each one.
const cache = new WeakMap()
const media = (query) => {
  const mm = typeof window !== 'undefined' ? window.matchMedia : undefined
  if (!mm) return null
  if (!cache.has(mm)) cache.set(mm, new Map())
  const lists = cache.get(mm)
  if (!lists.has(query)) lists.set(query, mm.call(window, query))
  return lists.get(query)
}

export function useMediaQuery(query) {
  const subscribe = useCallback((notify) => {
    const mql = media(query)
    if (!mql) return () => {}
    mql.addEventListener('change', notify)
    return () => mql.removeEventListener('change', notify)
  }, [query])
  return useSyncExternalStore(subscribe, () => media(query)?.matches ?? false, () => false)
}
