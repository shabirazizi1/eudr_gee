import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

// F1 (simplified): pick a role and enter a tailored workspace. No real auth in the MVP.
const DEFAULT_NAMES = { farmer: 'Ama Mensah', agent: 'Nadia Owusu', admin: 'Nadia Owusu' }
const HOME = { farmer: '/dashboard', agent: '/capture', admin: '/dashboard' }

export default function Login({ roles }) {
  const nav = useNavigate()
  const [role, setRole] = useState('agent')
  const [email, setEmail] = useState('nadia@koacocoa.com')

  const submit = (e) => {
    e.preventDefault()
    const user = { role, email, name: DEFAULT_NAMES[role] }
    try { localStorage.setItem('eudr_user', JSON.stringify(user)) } catch { /* ignore */ }
    nav(HOME[role])
  }

  return (
    <div className="login">
      <div className="login-hero">
        <span className="pill light">● ROOTED IN TRANSPARENCY</span>
        <div>
          <h1>Every bean has a beginning.</h1>
          <p>Connect cocoa to its origin. Map farms, understand forest risk, and build a more
            accountable supply chain.</p>
        </div>
      </div>
      <form className="login-form" onSubmit={submit}>
        <div className="eyebrow">COCOA TRACEABILITY PLATFORM</div>
        <h2>Welcome back.</h2>
        <p className="muted">Choose your role to enter your workspace.</p>
        <label className="field-label">I'm signing in as</label>
        <div className="roles">
          {Object.entries(roles).map(([key, label]) => (
            <button type="button" key={key}
              className={`role ${role === key ? 'active' : ''}`}
              onClick={() => setRole(key)}>
              <b>{label}</b>
              <small>{key === 'farmer' ? 'Manage my farm' : key === 'agent' ? 'Map & onboard' : 'Monitor sourcing'}</small>
            </button>
          ))}
        </div>
        <label className="field-label">Work email</label>
        <input value={email} onChange={(e) => setEmail(e.target.value)} type="email" required />
        <label className="field-label">Password</label>
        <input type="password" defaultValue="demo-password" />
        <button className="btn primary wide" type="submit">Sign in as {roles[role]} →</button>
        <p className="muted small center">Demo build: any password works.</p>
      </form>
    </div>
  )
}
