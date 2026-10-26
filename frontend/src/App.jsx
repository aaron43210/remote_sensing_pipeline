import { useEffect } from 'react'
import { BrowserRouter, HashRouter, Navigate, NavLink, Route, Routes, useLocation } from 'react-router'
import { Activity, History as HistoryIcon, LogOut, Map } from 'lucide-react'
import logo from './assets/icon.png'
import { DEMO, DEMO_NOTE } from './api'
import { AuthProvider, useAuth } from './auth'
import AuthPage from './pages/AuthPage'
import History from './pages/History'
import System from './pages/System'
import Workspace from './pages/Workspace'

// Static hosting (the demo Space) cannot serve index.html for deep links: use #/ routes there.
const Router = DEMO ? HashRouter : BrowserRouter

function RequireAuth({ children }) {
  const { signedIn, ready } = useAuth()
  const location = useLocation()
  if (!ready) return <div className="page muted">Loading…</div>
  return signedIn ? children : <Navigate to="/login" replace state={{ from: location.pathname }} />
}

function Shell({ children }) {
  const { user, logout } = useAuth()
  useEffect(() => {
    document.documentElement.dataset.theme = 'light'
    try { localStorage.removeItem('hydranet.theme') } catch { /* ignore */ }
  }, [])
  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand"><img src={logo} alt="HydraNet Logo" className="brand-logo" /> HydraNet Maps</div>
        <nav className="nav">
          <NavLink to="/" end><Map size={14} /> <span>Map</span></NavLink>
          <NavLink to="/jobs"><HistoryIcon size={14} /> <span>Jobs</span></NavLink>
          <NavLink to="/system"><Activity size={14} /> <span>Platform</span></NavLink>
        </nav>
        <div className="spacer" />
        <span className="secondary" style={{ fontSize: 13 }}>{user?.name}</span>
        {!DEMO && <button className="btn small" onClick={logout}><LogOut size={14} /> Sign out</button>}
      </header>
      {DEMO && (
        <div className="notice demo" role="note">
          <span>{DEMO_NOTE} Open a sample under <NavLink to="/jobs">Jobs</NavLink>. Code and model:{' '}
            <a href="https://github.com/aaron43210/remote_sensing_pipeline" target="_blank" rel="noreferrer">GitHub</a>{' · '}
            <a href="https://huggingface.co/aaronr43210/HydraNet-Hyperspectral" target="_blank" rel="noreferrer">model</a></span>
        </div>
      )}
      <main className="main">{children}</main>
    </div>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <Router>
        <Routes>
          <Route path="/login" element={<AuthPage />} />
          <Route path="/" element={<RequireAuth><Shell><Workspace /></Shell></RequireAuth>} />
          <Route path="/jobs/:id" element={<RequireAuth><Shell><Workspace /></Shell></RequireAuth>} />
          <Route path="/jobs" element={<RequireAuth><Shell><History /></Shell></RequireAuth>} />
          <Route path="/system" element={<RequireAuth><Shell><System /></Shell></RequireAuth>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Router>
    </AuthProvider>
  )
}
