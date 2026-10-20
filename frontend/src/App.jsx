import { useEffect, useState } from 'react'
import { BrowserRouter, Navigate, NavLink, Route, Routes, useLocation } from 'react-router'
import { Activity, History as HistoryIcon, LogOut, Map, Moon, Sun } from 'lucide-react'
import { AuthProvider, useAuth } from './auth'
import AuthPage from './pages/AuthPage'
import History from './pages/History'
import System from './pages/System'
import Workspace from './pages/Workspace'

function useTheme() {
  const [theme, setTheme] = useState(() => {
    try { return localStorage.getItem('hydranet.theme') || null } catch { return null }
  })
  useEffect(() => {
    if (theme) document.documentElement.dataset.theme = theme
    else delete document.documentElement.dataset.theme
    try { if (theme) localStorage.setItem('hydranet.theme', theme); else localStorage.removeItem('hydranet.theme') } catch { /* ignore */ }
  }, [theme])
  const dark = theme ? theme === 'dark' : window.matchMedia?.('(prefers-color-scheme: dark)').matches
  return [dark, () => setTheme(dark ? 'light' : 'dark')]
}

function RequireAuth({ children }) {
  const { signedIn, ready } = useAuth()
  const location = useLocation()
  if (!ready) return <div className="page muted">Loading…</div>
  return signedIn ? children : <Navigate to="/login" replace state={{ from: location.pathname }} />
}

function Shell({ children }) {
  const { user, logout, logoutAll } = useAuth()
  const [dark, toggle] = useTheme()
  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand"><span className="brand-dot" /> HydraNet Maps</div>
        <nav className="nav">
          <NavLink to="/" end><Map size={14} /> <span>Map</span></NavLink>
          <NavLink to="/jobs"><HistoryIcon size={14} /> <span>Jobs</span></NavLink>
          <NavLink to="/system"><Activity size={14} /> <span>Platform</span></NavLink>
        </nav>
        <div className="spacer" />
        <button className="btn small" onClick={toggle} aria-label="Toggle dark mode">{dark ? <Sun size={14} /> : <Moon size={14} />}</button>
        <span className="secondary" style={{ fontSize: 13 }}>{user?.name}</span>
        <button className="btn small" onClick={logout}><LogOut size={14} /> Sign out</button>
        <button className="btn small" onClick={logoutAll} title="End every session on every device">Sign out everywhere</button>
      </header>
      <main className="main">{children}</main>
    </div>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<AuthPage mode="login" />} />
          <Route path="/register" element={<AuthPage mode="register" />} />
          <Route path="/" element={<RequireAuth><Shell><Workspace /></Shell></RequireAuth>} />
          <Route path="/jobs/:id" element={<RequireAuth><Shell><Workspace /></Shell></RequireAuth>} />
          <Route path="/jobs" element={<RequireAuth><Shell><History /></Shell></RequireAuth>} />
          <Route path="/system" element={<RequireAuth><Shell><System /></Shell></RequireAuth>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}
