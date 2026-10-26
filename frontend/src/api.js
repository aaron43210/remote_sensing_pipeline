// The only module that talks to the gateway. Every endpoint the UI uses is here.
// Session model: the ACCESS token (15 min) lives in memory only, so an XSS
// payload cannot read it from storage. The long-lived REFRESH token is an
// HttpOnly, SameSite=Strict cookie scoped to /api/auth, which JavaScript cannot
// touch at all. A reload restores the session by calling /auth/refresh.

export class ApiError extends Error {
  constructor(status, message, body) {
    super(message)
    this.status = status
    this.body = body
    this.retryAfter = body?.retry_after_s
  }
}

let session = null            // { token, expires_at, user }
let onSessionChange = () => {}
let refreshing = null
export const setSessionListener = (fn) => { onSessionChange = fn }
export const getSession = () => session
export function setSession(s) { session = s; onSessionChange(s) }

const CSRF = { 'X-Requested-With': 'hydranet' }

// Demo build (VITE_DEMO=1, the Hugging Face Space): no server, no sign-in. Read-only
// answers come from precomputed files of real finished jobs under /demo/
// (made by scripts/export_demo.py); anything that would start work is refused.
export const DEMO = import.meta.env.VITE_DEMO === '1'
export const DEMO_NOTE = 'This is the read-only demo: it shows maps already made from real NASA scenes. ' +
  'Making new maps needs the full platform.'
const DEMO_USER = { id: 0, email: 'demo@hydranet', name: 'Demo visitor', role: 'user' }

async function demoFetch(method, path) {
  const json = (status, data) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })
  const file = async (name) => {
    const r = await fetch(`${import.meta.env.BASE_URL}demo/${name}`)
    return r.ok ? json(200, await r.json()) : json(404, { error: 'Not part of the demo.' })
  }
  const p = path.split('?')[0]
  if (p === '/auth/refresh') return json(200, { token: 'demo', expires_at: 4102444800, user: DEMO_USER })
  if (p === '/auth/logout') return new Response(null, { status: 204 })
  if (method !== 'GET') return json(403, { error: DEMO_NOTE })
  if (p === '/config' || p === '/model' || p === '/coverage' || p === '/jobs') return file(`${p.slice(1)}.json`)
  const m = p.match(/^\/jobs\/([\w-]+)(\/layers)?$/)
  if (m) return file(`jobs/${m[1]}${m[2] ? '.layers' : ''}.json`)
  if (p === '/me') return json(200, DEMO_USER)
  if (p === '/me/earthdata') return json(200, { connected: false })
  return json(403, { error: DEMO_NOTE })
}

async function raw(method, path, { body, headers = {}, signal } = {}) {
  if (DEMO) return demoFetch(method, path)
  const h = { Accept: 'application/json', ...headers }
  if (body !== undefined) h['Content-Type'] = 'application/json'
  try {
    return await fetch(`/api${path}`, { method, headers: h, body: body === undefined ? undefined : JSON.stringify(body),
      signal, credentials: 'same-origin' })
  } catch (e) {
    if (e.name === 'AbortError') throw e
    throw new ApiError(0, 'Cannot reach the server. Check your connection and try again.')
  }
}

async function parse(res) {
  const text = await res.text()
  let data = null
  try { data = text ? JSON.parse(text) : null } catch { data = { error: text } }
  if (!res.ok) throw new ApiError(res.status, data?.error || `Request failed (${res.status})`, data)
  return data
}

// One refresh at a time, shared by every request that hit an expired token.
export function refreshSession() {
  if (!refreshing) {
    refreshing = raw('POST', '/auth/refresh', { headers: CSRF })
      .then((res) => (res.status === 204 ? null : parse(res)))      // 204: no session cookie
      .then((s) => { setSession(s); if (!s) throw new ApiError(401, 'not signed in'); return s })
      .catch((e) => { setSession(null); throw e })
      .finally(() => { refreshing = null })
  }
  return refreshing
}

async function request(method, path, { body, headers = {}, auth = true, signal } = {}) {
  const withAuth = () => ({ ...headers, ...(auth && session ? { Authorization: `Bearer ${session.token}` } : {}) })
  if (auth && session && session.expires_at * 1000 < Date.now() + 30_000) {
    await refreshSession().catch(() => {})            // renew just before expiry
  }
  let res = await raw(method, path, { body, headers: withAuth(), signal })
  if (res.status === 401 && auth) {                    // expired or revoked: one silent refresh, then retry
    try {
      await refreshSession()
      res = await raw(method, path, { body, headers: withAuth(), signal })
    } catch { /* fall through: the 401 below signs the user out */ }
  }
  if (res.status === 401 && auth) setSession(null)
  return parse(res)
}

export const api = {
  config: () => request('GET', '/config', { auth: false }),
  model: () => request('GET', '/model', { auth: false }),
  logout: () => raw('POST', '/auth/logout', { headers: { ...CSRF, ...(session ? { Authorization: `Bearer ${session.token}` } : {}) } }),
  streamTicket: (id) => request('POST', `/jobs/${encodeURIComponent(id)}/stream-ticket`),
  me: () => request('GET', '/me'),
  earthdata: () => request('GET', '/me/earthdata'),
  connectEarthdata: (token) => request('POST', '/me/earthdata', { body: { token } }),
  disconnectEarthdata: () => request('DELETE', '/me/earthdata'),
  createJob: (payload, idempotencyKey) =>
    request('POST', '/jobs', { body: payload, headers: { 'Idempotency-Key': idempotencyKey } }),
  jobs: (before) => request('GET', `/jobs?limit=20${before ? `&before=${encodeURIComponent(before)}` : ''}`),
  job: (id) => request('GET', `/jobs/${encodeURIComponent(id)}`),
  cancelJob: (id) => request('DELETE', `/jobs/${encodeURIComponent(id)}`),
  layers: (id) => request('GET', `/jobs/${encodeURIComponent(id)}/layers`),
  coverage: (dateFrom, dateTo) => request('GET', `/coverage?date_from=${dateFrom}&date_to=${dateTo}`),
  stats: () => request('GET', '/stats'),
}

// Live job updates over Server-Sent Events, with polling as the fallback when
// the stream cannot be kept open (proxies, flaky networks). Returns a stop().
// The stream is opened with a single-use 30-second ticket, never with the token.
export function watchJob(id, onJob) {
  let stopped = false
  let es = null
  let timer = null
  const poll = async () => {
    if (stopped) return
    try {
      const j = await api.job(id)
      onJob(j)
      if (j.final) return
    } catch { /* keep trying: transient */ }
    timer = setTimeout(poll, 3000)
  }
  const open = async () => {
    try {
      const { ticket } = await api.streamTicket(id)
      if (stopped) return
      es = new EventSource(`/api/jobs/${encodeURIComponent(id)}/events?ticket=${ticket}`)
      es.addEventListener('job', (e) => { try { onJob(JSON.parse(e.data)) } catch { /* ignore malformed */ } })
      es.addEventListener('end', () => { es.close() })
      es.onerror = () => {             // tickets are single use: on a drop, get a new one
        if (stopped) return
        es.close()
        timer = setTimeout(open, 2000)
      }
    } catch {
      poll()
    }
  }
  if (typeof EventSource !== 'undefined') open()
  else poll()
  return () => {
    stopped = true
    if (es) es.close()
    if (timer) clearTimeout(timer)
  }
}

export function newIdempotencyKey() {
  return (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`).replace(/[^a-z0-9-]/gi, '')
}
