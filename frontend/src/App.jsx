import { useEffect, useState } from 'react'
import { Navigate, NavLink, Route, Routes, useNavigate } from 'react-router-dom'
import { api, flushOutbox, outboxCount } from './api.js'
import Login from './pages/Login.jsx'
import Capture from './pages/Capture.jsx'
import Result from './pages/Result.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Batches from './pages/Batches.jsx'
import BatchDetail from './pages/BatchDetail.jsx'
import BagPage from './pages/BagPage.jsx'

const ROLES = {
  warehouse: 'Warehouse operator',
  agent: 'Field agent',
  admin: 'Brand admin',
}

export function getUser() {
  try { return JSON.parse(localStorage.getItem('eudr_user')) } catch { return null }
}

function Shell({ user, children }) {
  const nav = useNavigate()
  const [mode, setMode] = useState(null)
  const [pending, setPending] = useState(outboxCount())
  const [online, setOnline] = useState(navigator.onLine)
  const [drawerOpen, setDrawerOpen] = useState(false)

  useEffect(() => {
    api.health().then((h) => setMode(h.mode)).catch(() => setMode('offline'))
    const sync = async () => {
      setOnline(navigator.onLine)
      await flushOutbox()
      setPending(outboxCount())
    }
    sync()
    const t = setInterval(() => setPending(outboxCount()), 3000)
    window.addEventListener('online', sync)
    window.addEventListener('offline', sync)
    return () => {
      clearInterval(t)
      window.removeEventListener('online', sync)
      window.removeEventListener('offline', sync)
    }
  }, [])

  const logout = () => {
    localStorage.removeItem('eudr_user')
    nav('/login')
  }
  const initials = user.name.split(' ').map((p) => p[0]).join('').slice(0, 2).toUpperCase()

  return (
    <div className="shell">
      <div className={`drawer-backdrop ${drawerOpen ? 'open' : ''}`} onClick={() => setDrawerOpen(false)} />
      <aside className={`sidebar ${drawerOpen ? 'open' : ''}`} onClick={() => setDrawerOpen(false)}>
        <div className="brand"><span className="brand-mark">❦</span> COCOA INTELLIGENCE</div>
        <div className="nav-label">WORKSPACE</div>
        <NavLink to="/dashboard" className="nav-item">Overview</NavLink>
        <NavLink to="/capture" className="nav-item">Farm registry</NavLink>
        <NavLink to="/farms" className="nav-item">Satellite verification</NavLink>
        <NavLink to="/batches" className="nav-item">Batches</NavLink>
        <div className="sidebar-foot">
          <div className="harvest">2026 / 27 harvest<br /><small>Ghana · Western Region</small></div>
          <button className="link-btn" onClick={logout}>Sign out</button>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="row" style={{ gap: 10 }}>
            <button type="button" className="menu-btn" aria-label="Open menu" onClick={() => setDrawerOpen(true)}>☰</button>
            <div className="org">Koa Cocoa Collective <span className="pill">Demo workspace</span></div>
          </div>
          <div className="topbar-right">
            {!online && <span className="pill warn">Offline</span>}
            {pending > 0 && <span className="pill warn">{pending} draft(s) waiting to sync</span>}
            {mode && (
              <span className={`pill ee-pill ${mode === 'gee' ? 'ok' : 'warn'}`}>
                <span className="ee-dot" />
                <span className="pill-text">
                  {mode === 'gee' ? 'Earth Engine: live' : mode === 'mock' ? 'Earth Engine: MOCK mode' : 'API offline'}
                </span>
              </span>
            )}
            <div className="user">
              <div><b>{user.name}</b><br /><small>{ROLES[user.role]}</small></div>
              <div className="avatar">{initials}</div>
            </div>
          </div>
        </header>
        <main className="content">{children}</main>
      </div>
    </div>
  )
}

function Protected({ children }) {
  const user = getUser()
  if (!user) return <Navigate to="/login" replace />
  return <Shell user={user}>{children}</Shell>
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login roles={ROLES} />} />
      <Route path="/bag/:id" element={<BagPage />} />
      <Route path="/dashboard" element={<Protected><Dashboard /></Protected>} />
      <Route path="/farms" element={<Protected><Dashboard listOnly /></Protected>} />
      <Route path="/capture" element={<Protected><Capture /></Protected>} />
      <Route path="/farms/:id" element={<Protected><Result /></Protected>} />
      <Route path="/batches" element={<Protected><Batches /></Protected>} />
      <Route path="/batches/:id" element={<Protected><BatchDetail /></Protected>} />
      <Route path="*" element={<Navigate to={getUser() ? '/dashboard' : '/login'} replace />} />
    </Routes>
  )
}
