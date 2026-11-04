import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api.js'
import RiskBadge from '../components/RiskBadge.jsx'

// F5 – Brand admin evidence & compliance dashboard (Figure 4)
const NEXT_ACTION = {
  'Not screened': 'Run satellite check',
  'Under review': 'Review flags',
  Verified: 'View evidence',
}

export default function Dashboard({ listOnly = false }) {
  const nav = useNavigate()
  const [summary, setSummary] = useState(null)
  const [farms, setFarms] = useState([])
  const [filter, setFilter] = useState('All')
  const [query, setQuery] = useState('')
  const [running, setRunning] = useState(null)
  const [error, setError] = useState('')

  const load = () => Promise.all([api.summary(), api.farms()])
    .then(([s, f]) => { setSummary(s); setFarms(f) })
    .catch((e) => setError(e.message))
  useEffect(() => { load() }, [])

  const verify = async (id) => {
    setRunning(id)
    try { await api.verify(id); await load() } catch (e) { setError(e.message) } finally { setRunning(null) }
  }

  const shown = farms.filter((f) =>
    (filter === 'All' || (filter === 'Not screened' ? !f.risk_level : f.risk_level === filter)) &&
    `${f.name} ${f.farmer_name} ${f.code}`.toLowerCase().includes(query.toLowerCase()))

  const d = summary?.distribution || { Low: 0, Medium: 0, High: 0 }
  const total = (d.Low + d.Medium + d.High) || 1

  return (
    <div>
      <div className="crumbs">Workspace / {listOnly ? 'Satellite verification' : 'Overview'}</div>
      <div className="page-head">
        <div>
          <h1>{listOnly ? 'Satellite verification queue.' : 'Know your cocoa. Know your impact.'}</h1>
          <p className="muted">Your sourcing portfolio at a glance · 2026 / 27 harvest · Ghana</p>
        </div>
        <button className="btn primary" onClick={() => nav('/capture')}>+ Add farm</button>
      </div>
      {error && <p className="bad-text">{error}</p>}

      {!listOnly && summary && (
        <>
          <div className="kpis">
            <div className="card pad"><small className="muted">Registered farms</small><b>{summary.farms}</b></div>
            <div className="card pad"><small className="muted">Mapped sourcing area</small><b>{summary.hectares} <small>ha</small></b></div>
            <div className="card pad"><small className="muted">Low satellite risk</small><b>{summary.low_risk_pct}%</b>
              <small className="muted">of {summary.screened} screened farms</small></div>
            <div className="card pad"><small className="muted">Needs attention</small><b>{summary.under_review + summary.not_screened}</b>
              <small className="muted">{summary.under_review} under review · {summary.not_screened} not screened</small></div>
          </div>
          <div className="card pad">
            <div className="card-head tight"><b>Satellite risk distribution</b><small className="muted">{summary.screened} screened farms</small></div>
            <div className="dist">
              <i className="low" style={{ width: `${d.Low / total * 100}%` }} />
              <i className="medium" style={{ width: `${d.Medium / total * 100}%` }} />
              <i className="high" style={{ width: `${d.High / total * 100}%` }} />
            </div>
            <div className="row gap small">
              <span><b>{d.Low}</b> Low</span><span><b>{d.Medium}</b> Medium</span><span><b>{d.High}</b> High</span>
            </div>
          </div>
        </>
      )}

      <section className="card">
        <div className="card-head">
          <b>Farm portfolio <span className="pill">{farms.length} farms</span></b>
          <div className="row gap">
            <input className="search" placeholder="Search farm or farmer…" value={query} onChange={(e) => setQuery(e.target.value)} />
            <select value={filter} onChange={(e) => setFilter(e.target.value)}>
              {['All', 'Low', 'Medium', 'High', 'Not screened'].map((o) => <option key={o}>{o}</option>)}
            </select>
          </div>
        </div>
        <table className="table">
          <thead>
            <tr><th>Farm / farmer</th><th>Area</th><th>Satellite risk</th><th>Score</th><th>Status</th><th>Last screened</th><th>Next action</th></tr>
          </thead>
          <tbody>
            {shown.map((f) => (
              <tr key={f.id}>
                <td><Link to={`/farms/${f.id}`}><b>{f.name}</b></Link><br /><small className="muted">{f.code} · {f.farmer_name}</small></td>
                <td>{f.area_ha} ha</td>
                <td><RiskBadge level={f.risk_level} /></td>
                <td>{f.risk_score ?? '–'}</td>
                <td>{f.status}</td>
                <td>{f.last_screened ? new Date(f.last_screened).toLocaleDateString() : '–'}</td>
                <td>
                  {!f.risk_level ? (
                    <button className="link-btn" disabled={running === f.id} onClick={() => verify(f.id)}>
                      {running === f.id ? 'Screening…' : 'Run satellite check'}
                    </button>
                  ) : f.status === 'Verified' ? (
                    <a className="link-btn" href={api.reportUrl(f.id)}>Export evidence ⤓</a>
                  ) : (
                    <Link className="link-btn" to={`/farms/${f.id}`}>{NEXT_ACTION[f.status]} →</Link>
                  )}
                </td>
              </tr>
            ))}
            {!shown.length && <tr><td colSpan="7" className="muted center">No farms match.</td></tr>}
          </tbody>
        </table>
      </section>
      <p className="disclaimer small">Risk scores are decision-support indicators, not legal certification.</p>
    </div>
  )
}
