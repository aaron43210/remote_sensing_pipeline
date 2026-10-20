import { useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'
import logo from '../assets/icon.png'
import { useAuth } from '../auth'

// Only same-site paths: never "//evil.example", "/\\evil", or an absolute URL (open redirect).
function safePath(p) {
  return typeof p === 'string' && /^\/(?![\/\\])[\w\-./?=&%]*$/.test(p) ? p : '/'
}

export default function AuthPage({ mode }) {
  const auth = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [name, setName] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const register = mode === 'register'

  async function submit(e) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      if (register) await auth.register(email, password, name)
      else await auth.login(email, password)
      navigate(safePath(location.state?.from), { replace: true })
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth">
      <form className="card" onSubmit={submit}>
        <div className="brand" style={{ gap: 10, marginBottom: 4 }}><img src={logo} alt="HydraNet Logo" className="brand-logo large" /> HydraNet Maps</div>
        <h1>{register ? 'Create an account' : 'Sign in'}</h1>
        {register && (
          <label className="field">Name
            <input type="text" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} maxLength={100} />
          </label>
        )}
        <label className="field">Email
          <input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label className="field">Password{register ? ' (10 characters or more)' : ''}
          <input type="password" autoComplete={register ? 'new-password' : 'current-password'} required minLength={register ? 10 : 8} maxLength={72}
                 value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && <div className="notice error" role="alert">{error}</div>}
        <button className="btn primary block" disabled={busy}>{busy ? 'Please wait…' : register ? 'Create account' : 'Sign in'}</button>
        <p className="muted" style={{ fontSize: 13 }}>
          {register ? <>Already registered? <Link to="/login">Sign in</Link></> : <>New here? <Link to="/register">Create an account</Link></>}
        </p>
      </form>
    </div>
  )
}
