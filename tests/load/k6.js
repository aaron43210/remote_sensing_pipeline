// Load test: many users at once, like the UI behaves.
// NOTE: from ONE machine every virtual user shares one IP, so the per-IP sign-in
// limit (security) caps sign-ins at 20/min. For capacity runs target the
// gateway directly with the limits lifted (see README); through the edge this
// script instead demonstrates that the limit holds.
//   docker run --rm --network hydranet_default -v $PWD/tests/load:/s grafana/k6:0.54.0 run /s/k6.js
// Sign-in uses the test IdP (docker compose --profile test): the gateway's
// redirect is followed to mock-oidc and back, as a browser would.
// Each virtual user: sign in -> config/model -> submit a job (areas repeat on
// purpose: identical requests are coalesced / reused) -> poll status every 2 s
// like the UI's fallback -> fetch layers. Thresholds fail the run if the API
// gets slow or errors under load.
import http from 'k6/http'
import { check, sleep } from 'k6'
import { Counter, Trend } from 'k6/metrics'

const BASE = __ENV.BASE || 'http://api_gateway:8000'
const jobSeconds = new Trend('job_seconds', true)
const jobsDone = new Counter('jobs_completed')
const jobsReused = new Counter('jobs_reused_or_coalesced')
const rejected = new Counter('jobs_rejected_capacity')

export const options = {
  scenarios: {
    users: { executor: 'ramping-vus', startVUs: 0, gracefulRampDown: '30s',
      stages: [{ duration: '30s', target: __ENV.VUS ? +__ENV.VUS : 300 }, { duration: '2m', target: __ENV.VUS ? +__ENV.VUS : 300 },
        { duration: '20s', target: 0 }] },
  },
  thresholds: {
    'http_req_duration{kind:api}': ['p(95)<500'],
    'http_req_failed{kind:api}': ['rate<0.01'],
    'http_req_duration{name:signin}': ['p(95)<2000'],
    'http_req_duration{name:job}': ['p(95)<300'],
    'http_req_duration{name:submit}': ['p(95)<500'],
    'http_req_duration{name:layers}': ['p(95)<500'],
    'http_req_duration{name:config}': ['p(95)<300'],
  },
}

const json = { headers: { 'Content-Type': 'application/json' }, tags: { kind: 'api' } }
const named = (o, name) => ({ ...o, tags: { ...o.tags, name } })
// AREAS=40 (default): many users ask for the same places -> coalescing and reuse.
// DISTINCT=1: every job is a new area -> measures real processing throughput.
// 40 distinct areas in the Central Valley: many users ask for the same ones
const AREAS = Array.from({ length: 40 }, (_, i) => {
  const lon = -121.0 + (i % 8) * 0.25, lat = 36.0 + Math.floor(i / 8) * 0.3
  return [lon, lat, +(lon + 0.05).toFixed(3), +(lat + 0.05).toFixed(3)]
})

let token = null   // per virtual user: sign in once, then keep using the app like a person
const IDP = __ENV.IDP || 'http://mock-oidc:9998'

function signIn(email) {
  const noFollow = { redirects: 0, tags: { kind: 'api', name: 'signin' } }
  const start = http.get(`${BASE}/api/auth/google/login`, noFollow)
  if (start.status !== 302) return null
  // inside the compose network the IdP's browser URL (localhost:9998) is mock-oidc:9998
  const authz = http.get(start.headers.Location.replace('http://localhost:9998', IDP) + `&login_hint=${encodeURIComponent(email)}`, noFollow)
  const back = http.get(authz.headers.Location.replace('http://localhost:3000', BASE), noFollow)
  if (back.status !== 303) return null
  const r = http.post(`${BASE}/api/auth/refresh`, null, { headers: { 'X-Requested-With': 'hydranet' }, tags: { kind: 'api', name: 'signin' } })
  return r.status === 200 ? r.json('token') : null
}

export default function () {
  if (!token) {
    token = signIn(`load-${__VU}-${Date.now()}@example.com`)
    if (!check(token, { 'signed in': (t) => !!t })) { sleep(5); return }   // rate-limited: back off
  }
  const auth = { headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }, tags: { kind: 'api' } }
  http.get(`${BASE}/api/config`, named(json, 'config'))
  http.get(`${BASE}/api/model`, named(json, 'model'))
  const area = __ENV.DISTINCT
    ? (() => { const k = __VU * 1000 + __ITER, lon = -122 + (k % 97) * 0.06, lat = 35 + (Math.floor(k / 97) % 60) * 0.06
      return [+lon.toFixed(3), +lat.toFixed(3), +(lon + 0.04).toFixed(3), +(lat + 0.04).toFixed(3)] })()
    : AREAS[Math.floor(Math.random() * AREAS.length)]
  const t0 = Date.now()
  const sub = http.post(`${BASE}/api/jobs`, JSON.stringify({ bbox: area, tasks: ['agriculture', 'mineral', 'thermal'], source: 'fixture' }),
    { headers: auth.headers, tags: { kind: 'submit', name: 'submit' } })
  if (sub.status === 503 || sub.status === 429) { rejected.add(1); return }   // admission control working as designed
  if (!check(sub, { accepted: (r) => r.status === 202 })) return
  const id = sub.json('job_id')
  if (sub.json('reused_from')) jobsReused.add(1)
  sleep(1)
  for (let i = 0; i < 150; i++) {
    const r = http.get(`${BASE}/api/jobs/${id}`, named(auth, 'job'))
    if (r.status === 200 && r.json('final')) {
      jobSeconds.add(Date.now() - t0)   // ms (time trend)
      if (check(r, { completed: (x) => x.json('status') === 'COMPLETED' })) {
        jobsDone.add(1)
        check(http.get(`${BASE}/api/jobs/${id}/layers`, named(auth, 'layers')), { layers: (x) => x.status === 200 && x.json('layers').length > 0 })
      }
      sleep(3)          // a person looks at the maps before the next request
      return
    }
    sleep(2)
  }
}
