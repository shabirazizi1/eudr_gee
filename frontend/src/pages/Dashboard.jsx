import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api.js'
import RiskBadge from '../components/RiskBadge.jsx'

// F5 – Brand admin evidence & compliance dashboard (Figure 4)
const NEXT_ACTION = {
  'Not screened': 'Run satellite check',
  'Under review': 'Review flags',
  Verified: 'View evidence',
  'Verified (manual review)': 'View evidence',
  Excluded: 'View decision',
}
const BATCHABLE_STATUSES = ['Verified', 'Verified (manual review)']

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

  const matchesFilter = (f) => {
    if (filter === 'All') return true
    if (filter === 'Not screened') return !f.risk_level
    if (filter === 'Review queue') return f.status === 'Under review'
    return f.risk_level === filter
  }
  const shown = farms.filter((f) =>
    matchesFilter(f) && `${f.name} ${f.farmer_name} ${f.code}`.toLowerCase().includes(query.toLowerCase()))

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
              <small className="muted">
                {summary.under_review} under review · {summary.not_screened} not screened · {summary.needs_field_audit} needs field audit
                · {summary.reviewed} reviewed · {summary.excluded} excluded
              </small></div>
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
              {['All', 'Low', 'Medium', 'High', 'Not screened', 'Review queue'].map((o) => <option key={o}>{o}</option>)}
            </select>
          </div>
        </div>
        <table className="table dash-table">
          <thead>
            <tr><th>Farm / farmer</th><th>Area</th><th>Satellite risk</th><th>Score</th><th>Status</th><th>Last screened</th><th>Next action</th></tr>
          </thead>
          <tbody>
            {shown.map((f) => (
              <tr key={f.id}>
                <td data-label="Farm">
                  <Link to={`/farms/${f.id}`}><b>{f.name}</b></Link><br /><small className="muted">{f.code} · {f.farmer_name}</small>
                  {f.needs_field_audit && <div><span className="pill warn small">⚠ Needs field audit</span></div>}
                  {f.status === 'Verified (manual review)' && <div><span className="pill ok small">✓ Manually cleared</span></div>}
                  {f.status === 'Excluded' && <div><span className="pill warn small">⛔ Excluded</span></div>}
                </td>
                <td data-label="Area">{f.area_ha} ha</td>
                <td data-label="Risk"><RiskBadge level={f.risk_level} /></td>
                <td data-label="Score">{f.risk_score ?? '–'}</td>
                <td data-label="Status">{f.status}</td>
                <td data-label="Last screened">{f.last_screened ? new Date(f.last_screened).toLocaleDateString() : '–'}</td>
                <td data-label="Action">
                  {!f.risk_level ? (
                    <button className="link-btn" disabled={running === f.id} onClick={() => verify(f.id)}>
                      {running === f.id ? 'Screening…' : 'Run satellite check'}
                    </button>
                  ) : BATCHABLE_STATUSES.includes(f.status) ? (
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
