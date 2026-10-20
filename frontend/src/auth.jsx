import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import { api, getSession, refreshSession, setSession, setSessionListener } from './api'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [session, setState] = useState(getSession())
  const [ready, setReady] = useState(false)

  useEffect(() => {
    setSessionListener(setState)
    // a reload: restore the session from the HttpOnly refresh cookie, if any
    refreshSession().catch(() => {}).finally(() => setReady(true))
  }, [])

  const value = useMemo(() => ({
    user: session?.user || null,
    signedIn: !!session,
    ready,
    async login(email, password) { setSession(await api.login(email, password)) },
    async register(email, password, name) { setSession(await api.register(email, password, name)) },
    async logout() { await api.logout().catch(() => {}); setSession(null) },
  }), [session, ready])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  return useContext(AuthContext)
}
