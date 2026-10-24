import { useEffect, useRef, useState } from 'react'
import { ExternalLink, KeyRound } from 'lucide-react'
import { api } from '../api'
import { dateOnly } from '../lib/format'

// Asks for the user's NASA Earthdata token the first time they choose an EMIT
// map (Minerals, Agriculture), or when the saved one has expired. The gateway
// checks it with NASA before saving it encrypted. Native <dialog>: Esc, focus
// trapping and the backdrop come from the browser.
//   reason: the task being switched on, 'submit' (the server asked for it) or 'change'
//   onDone(status | null): connected, or cancelled
export default function EarthdataDialog({ reason, status, onDone }) {
  const ref = useRef(null)
  const result = useRef(null)
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    const d = ref.current
    if (reason && !d.open) d.showModal()
    if (!reason && d.open) d.close()
  }, [reason])

  const close = () => ref.current.close()
  const closed = () => {                                   // every way out: Submit, cancel, Esc, backdrop
    onDone(result.current)
    result.current = null
    setToken(''); setError(null)
  }

  async function connect(e) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      result.current = await api.connectEarthdata(token.trim())
      close()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const what = { agriculture: 'Agriculture maps use', mineral: 'Mineral maps use' }[reason]
    || 'Agriculture and mineral maps use'
  return (
    // a click on the dialog element itself is a click on the backdrop: the form fills the box
    <dialog ref={ref} className="dialog" aria-labelledby="nasa-title" onClose={closed}
      onClick={(e) => { if (e.target === ref.current) close() }}>
      <form className="dialog-body" onSubmit={connect}>
        <h2 id="nasa-title" className="row"><KeyRound size={16} /> Connect your NASA account</h2>
        <p className="secondary">
          {status?.expired && `Your NASA token expired on ${dateOnly(status.expires_at)}. `}
          {reason === 'change' ? 'Replace your saved NASA Earthdata token. ' : `${what} NASA EMIT hyperspectral images. `}
          They are free, but NASA asks each user to download them with their own Earthdata account.
          Thermal maps (Landsat) need no account.
        </p>
        <label className="field">Earthdata token
          <input type="password" value={token} onChange={(e) => setToken(e.target.value)} required
            autoComplete="off" spellCheck={false} placeholder="eyJ0eXAiOiJKV1QiLCJvcmlnaW4iOiJFYXJ0aGRhdGEgTG9naW4i…" />
        </label>
        <p className="muted" style={{ fontSize: 12 }}>
          Get one: sign in (or sign up, free) at{' '}
          <a href="https://urs.earthdata.nasa.gov/" target="_blank" rel="noreferrer">
            urs.earthdata.nasa.gov <ExternalLink size={11} /></a>, choose <b>Generate Token</b> and copy it.
          It is valid for 60 days, saved encrypted, and used only to download EMIT scenes for your jobs.
        </p>
        {error && <div className="notice error" role="alert">{error}</div>}
        <div className="row" style={{ justifyContent: 'flex-end' }}>
          <button type="button" className="btn" onClick={close}>
            {reason === 'change' ? 'Cancel' : 'Temperature results only'}
          </button>
          <button className="btn primary" disabled={busy || !token.trim()}>
            {busy ? 'Checking with NASA…' : 'Submit'}
          </button>
        </div>
      </form>
    </dialog>
  )
}
