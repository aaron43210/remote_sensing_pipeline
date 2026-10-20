# HydraNet Maps: Production Frontend Plan

**Goal:** turn the working frontend into a polished, interactive product that
looks credible to a reviewer in the first five seconds and stays fast with
many users. **The app's data flows stay as they are:** every map, number and
legend keeps coming from the real API (already tested end to end). This plan
changes how it looks, feels and performs.

## 1. Where we start (honest audit of today's UI)

| Area | Today | Gap |
|---|---|---|
| Visual design | Clean, token-based, light + dark, validated colour palette | No brand identity, flat hierarchy, no landing page, generic sign-in |
| Map | Leaflet + one PNG overlay per layer, fixed resolution | Blurry when zoomed, no side-by-side comparison, no hover values, overlays reload on every switch |
| Interactivity | Draw box, layer select, opacity, pixel inspector, live timeline | No swipe/compare, no animation between states, no keyboard shortcuts, no shareable links |
| Feedback | Plain text while loading | No skeletons, no toasts, no "job finished" notification when the tab is in the background |
| Mobile | Stacks into one column | Not designed for touch; the sidebar pushes the map off screen |
| Accessibility | Labels, focus rings, colour-blind-safe palette, table views | Not audited (no axe/keyboard pass), map not usable by keyboard |
| Performance | 471 kB JS (147 kB gzip), one bundle | No code splitting, no tile caching, no performance budget |
| Quality gates | Playwright smoke test, 5 unit tests | No visual regression, no accessibility tests in CI |

## 2. Design direction

**Character:** a precise scientific instrument, not a marketing site. Calm
surfaces, one strong accent, the map as the hero, data loud and chrome quiet.

| Decision | Choice | Why |
|---|---|---|
| Brand | Wordmark "HydraNet" + a three-headed spectral mark (three arcs = three heads) | Memorable, says "one model, three maps" |
| Type | **Inter** (UI) + **JetBrains Mono** (coordinates, IDs, numbers in tables), self-hosted | Excellent at small sizes, tabular figures |
| Colour | Keep today's validated data palette **unchanged** for maps and charts. Add a brand accent (deep spectral blue) and neutral greys for chrome | Map colours already pass colour-blindness checks; the brand never competes with data |
| Surfaces | Glass-light panels floating over the map (blurred backdrop, 1px hairline, soft shadow) | Keeps the map full-bleed, which is the "wow" moment |
| Motion | 150–250 ms ease-out for panels, 400 ms for map flights; respect `prefers-reduced-motion` | Feels alive without slowing work |
| Density | Two modes: *Comfortable* (default) and *Compact* (analysts) | Long result panels fit on laptops |
| Dark mode | First-class; map basemap switches to a dark style automatically | Already supported by tokens |

Deliverable of this step: a design-token file (colour, type scale, spacing
4-px grid, radii, shadows, motion), a component sheet, and Figma or HTML
mock-ups of 6 key screens, signed off before building.

## 3. Information architecture

```
/                 Landing (public): what it does, live demo map, verified accuracy, sign-in
/login /register  Split-screen auth with a live mini-map, SSO-ready
/app              Workspace: full-screen map + floating panels
/app/jobs/:id     Results for one job (shareable link)
/app/compare/:id  NN vs classical, side by side (swipe)
/app/jobs         History: cards with thumbnails, filters, search
/app/platform     Throughput, model card, verification gates (admin: all users)
/docs             How the maps are made, data sources, limitations
```

## 4. Features, by screen

### 4.1 Landing page (new)
- Full-bleed animated hero: a real result over the Central Valley, cycling between the agriculture, mineral and thermal layers.
- Three cards, one per map, each with the classical method it reproduces and its measured agreement (live from `/api/model`).
- "How it works": a five-step animated pipeline diagram (EMIT/Landsat → clean → HydraNet → verify → map).
- An "Open demo" button that runs a synthetic scene with no sign-up (read-only demo account, labelled as synthetic).
- Footer: data credits (NASA, USGS, Esri), licence, status page link.

### 4.2 Sign-in / register (redesign)
- Split screen: form on the left, a slowly panning map with real overlays on the right.
- Show/hide password, a strength meter on register, inline validation, caps-lock warning, and an error state that never reveals whether an email exists.
- Ready for SSO later (Google / institutional SAML) behind one button. Backend work is listed in §7.

### 4.3 Workspace (the core screen)
- **Full-screen map** with floating panels: area & request (left), results (right, collapsible), timeline (bottom sheet).
- **Area tools:** draw a rectangle, draw a polygon (clipped to the bbox server-side), search a place name (geocoder), "use my location", paste coordinates, snap to the size limit with a live km² readout.
- **Imagery preview before submitting:** the available EMIT/Landsat dates for the box (from `/api/catalog`) as a mini timeline, with cloud cover shown per date.
- **Live job timeline:** animated stage chips, ETA from the platform's p50 latency, queue position, and a browser notification + favicon badge when maps are ready.
- **Command palette (⌘K):** new area, open job, switch layer, toggle theme, jump to a place.
- **Keyboard shortcuts:** `D` draw, `1/2/3` task tabs, `[` `]` previous/next layer, `O` opacity, `I` inspector, `Esc` cancel.

### 4.4 Results
- **Sharp at any zoom:** serve maps as XYZ tiles straight from the COGs (TiTiler, §7) instead of one PNG, using the same colour ramps.
- **Hover readout:** values under the cursor, live, in a small tooltip (read from the COG). Click pins the full pixel inspector.
- **Swipe compare:** drag a divider to compare two layers, two dates, or **HydraNet vs the classical method** for the same area. This is the visual proof of "same map as the normal way".
- **Layer gallery:** thumbnails instead of a dropdown, grouped (main, uncertainty, per-mineral abundance).
- **Charts:** histogram with brushing (drag a range → the map highlights those pixels), mineral composition bars (click → highlight on the map), and a 3-head summary strip. Charts stay on the validated palette with table views.
- **Verification card:** a badge per map ("matches classical: 99.9 %") that expands into the per-layer table.
- **Export:** GeoTIFF (today), PNG snapshot of the current view with legend, and a one-page PDF report (map, legend, stats, verification, data credits).
- **Share link:** a read-only signed URL for one job (expiring), with the map view state (center, zoom, layer, opacity) in the URL.

### 4.5 History
- Card grid with a thumbnail per job, status chip, area name (reverse-geocoded), tasks, date.
- Filter by status, task and date; search by place; bulk delete.
- Re-run with new dates (pre-filled form).

### 4.6 Platform (admin)
- Live charts: jobs per minute, p50/p95 time to maps, queue depth, per-task outcomes (from Prometheus or `/api/stats` polling).
- Model card with gate tables, promotion history, and shadow-agreement trend per layer (7 days).
- Users table (admin only): jobs, last seen, quota.

### 4.7 Mobile
- Bottom-sheet layout: the map on top, a draggable sheet for request and results.
- Touch drawing (two-tap rectangle), pinch zoom, large tap targets (44 px).
- Read-only view of results is the priority on phones; drawing remains possible.

### 4.8 System states (everywhere)
- Skeleton loaders that match each panel's shape; empty states with one clear action; error states with the request ID and a retry button.
- Toasts for background events (job finished, link copied, presigned URL refreshed).
- An offline banner, and automatic SSE reconnect with "reconnecting…".

## 5. Technology choices

| Need | Choice | Notes |
|---|---|---|
| Framework | Keep **React 19 + Vite** | No rewrite |
| Map | **MapLibre GL JS** (replace Leaflet) | WebGL, smooth zoom, raster tiles, swipe compare (`maplibre-gl-compare`), dark vector basemap |
| Basemaps | Esri World Imagery (today) + OpenFreeMap / Protomaps vector styles (free, no key) | Dark style for dark mode |
| Components | **Radix UI primitives** (dialog, popover, tabs, tooltip, toast) + our tokens | Accessible behaviour without a heavy design system |
| Styling | Keep CSS custom properties; move to **CSS Modules** per component | No runtime CSS cost |
| Motion | **Motion** (formerly Framer Motion), loaded per route | Respects reduced motion |
| Server state | **TanStack Query** | Caching, retries and refetch of presigned URLs in one place |
| Routing | React Router (today) with route-level code splitting | |
| Charts | Keep our dependency-free SVG charts (validated palette); add brushing | |
| Icons | lucide-react (today) | |
| Tests | Vitest, Playwright, **@axe-core/playwright**, Playwright visual snapshots | |

## 6. Quality bars (acceptance criteria)

| Category | Target |
|---|---|
| Performance | Lighthouse ≥ 90 on landing and workspace; LCP < 2.5 s, INP < 200 ms, CLS < 0.1 on a mid-range laptop over 4G; initial JS ≤ 200 kB gzip (map loaded on demand) |
| Accessibility | WCAG 2.2 AA: axe with 0 serious issues, full keyboard path (draw by coordinates, submit, read results), visible focus, 4.5:1 text contrast, screen-reader labels on every control, map alternatives (tables) |
| Responsiveness | 360 px → 2560 px, no horizontal scroll; touch targets ≥ 44 px |
| Reliability | No console errors; every API failure shows a recoverable state; SSE reconnects |
| Browsers | Last 2 versions of Chrome, Edge, Firefox, Safari (macOS + iOS) |
| Security | Strict CSP (no inline scripts), presigned URLs only, no tokens in URLs except the SSE stream, cookies `HttpOnly` once auth moves to cookies (§7) |

## 7. Backend changes this design needs

| Change | Why | Effort |
|---|---|---|
| **TiTiler** service (reads the result COGs from S3/MinIO, serves XYZ tiles with our colour ramps), behind nginx/CloudFront with caching | Sharp maps at every zoom, fast layer switching | 1–2 days |
| `/api/jobs/:id/compare` returning the classical-method layers for that job (run on the full scene in the worker, stored like other layers) | Swipe NN vs classical | 1 day |
| `/api/share` signed read-only links (expiry, revoke) | Sharing | 1 day |
| Auth via `HttpOnly` cookie + CSRF token (keep Bearer for API clients) | Removes the token from JavaScript and from the SSE URL | 1 day |
| Thumbnail per job (small PNG at completion) | History cards | 0.5 day |
| Reverse geocoding of the bbox (cached; Nominatim or OSM-based) | Readable job names | 0.5 day |
| Demo account + rate-limited demo route | "Open demo" without sign-up | 0.5 day |
| Optional: SSO (OIDC) | Institutional sign-in | 2 days |

## 8. Phases

| Phase | Scope | Done when | Estimate |
|---|---|---|---|
| **F0 Design** | Tokens, type, brand mark, component sheet, mock-ups of 6 screens | Mock-ups approved | 3–4 days |
| **F1 Foundations** | CSS Modules, Radix primitives, TanStack Query, route code-splitting, error/empty/skeleton states, toasts, CSP | All current tests pass; bundle budget met | 3 days |
| **F2 Map engine** | MapLibre migration, TiTiler tiles, dark basemap, hover readout, layer gallery | Same features as today, sharp at every zoom, no regressions in e2e | 4–5 days |
| **F3 Signature interactions** | Swipe compare (incl. NN vs classical), histogram brushing → map highlight, command palette, shortcuts, polygon draw, geocoder | Usability test with 3 people: each finds and compares a map unaided in < 2 min | 5 days |
| **F4 Public face** | Landing page, auth redesign, demo mode, `/docs` | Lighthouse ≥ 90, copy reviewed | 3–4 days |
| **F5 History, sharing, export** | Cards + thumbnails, filters, share links, PNG/PDF export, browser notifications | Shared link opens the exact same view for another user | 3 days |
| **F6 Mobile** | Bottom-sheet layout, touch drawing | Passes on iPhone and Android device tests | 2–3 days |
| **F7 Hardening** | axe + keyboard audit, visual regression in CI, performance budget in CI, cross-browser pass | All §6 bars met in CI | 2–3 days |

Total: about **5–6 weeks for one frontend developer** (F0 and the backend work
in §7 can run in parallel). Critical path: F0 → F1 → F2 → F3.

## 9. Testing added

- **Visual regression:** Playwright screenshots of 12 key states (light/dark, desktop/mobile) compared on every PR.
- **Accessibility:** axe on every route, plus a scripted keyboard-only journey (sign in → coordinates → submit → read a value).
- **Interaction tests:** swipe compare, brushing, command palette, share link round-trip.
- **Performance budget:** bundle size check and Lighthouse CI on the landing page and workspace.
- The existing end-to-end API test and browser smoke test keep running unchanged.

## 10. Decisions needed before F0

1. **Brand:** project-neutral "HydraNet" (assumed here) or a university-branded version? University branding requires their approval.
2. **Public demo:** allowed (synthetic scenes only, labelled)?
3. **Map stack:** OK to replace Leaflet with MapLibre GL and add TiTiler? (Recommended: it is what makes the maps sharp and the swipe compare possible.)
4. **Sign-in:** email/password only for now, or institutional SSO in this phase?
