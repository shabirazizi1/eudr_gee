import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api.js'
import { getUser } from '../App.jsx'
import RiskBadge from '../components/RiskBadge.jsx'

// HLR-02 / DEL-03 / DEL-06 – bundle verified farms into a traceable shipment batch
const DEFAULT_BUYER = 'Chocolaterie Vandermeer, Ghent, Belgium'
const DEFAULT_SEASON = '2026/27'
const KG_PER_BAG = 64
// Mass-balance override reasons (issue #6) -- must match backend OVERRIDE_REASONS.
const OVERRIDE_REASONS = ['Second harvest', 'Yield above average', 'Data correction', 'Other']

function disabledReason(farm) {
  if (farm.status === 'Verified' || farm.status === 'Verified (manual review)') return null
  if (!farm.risk_level) return 'Not screened'
  if (farm.status === 'Excluded') return `Excluded – ${farm.latest_review?.reason || 'manual review'}`
  return `${farm.risk_level} risk – under review`
}

export default function Batches() {
  const nav = useNavigate()
  const isAdmin = getUser()?.role === 'admin'
  const [batches, setBatches] = useState([])
  const [farms, setFarms] = useState([])
  const [creating, setCreating] = useState(false)
  const [buyer, setBuyer] = useState(DEFAULT_BUYER)
  const [season, setSeason] = useState(DEFAULT_SEASON)
  const [bags, setBags] = useState({}) // farmId -> bag count string
  const [overrides, setOverrides] = useState({}) // farmId -> { reason, note }
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const loadFarms = (s) => api.farms(s).then(setFarms)
  useEffect(() => { api.batches().then(setBatches); loadFarms(season) }, []) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (creating) loadFarms(season) }, [season, creating])

  const toggleFarm = (id) => {
    setBags((b) => {
      const next = { ...b }
      if (next[id] != null) delete next[id]
      else next[id] = '10'
      return next
    })
  }
  const setBagCount = (id, v) => setBags((b) => ({ ...b, [id]: v }))
  const setOverride = (id, patch) => setOverrides((o) => ({ ...o, [id]: { ...o[id], ...patch } }))

  const selected = Object.entries(bags).filter(([, v]) => Number(v) > 0)
  const totalBags = selected.reduce((s, [, v]) => s + Number(v), 0)
  const farmById = Object.fromEntries(farms.map((f) => [f.id, f]))

  const kgFor = (farmId) => Number(bags[farmId] || 0) * KG_PER_BAG
  const remainingFor = (farmId) => farmById[farmId]?.remaining_capacity_kg
  const overCapacity = (farmId) => {
    const remaining = remainingFor(farmId)
    return remaining != null && kgFor(farmId) > remaining
  }
  const overrideReady = (farmId) => {
    const o = overrides[farmId]
    return Boolean(o?.reason && o?.note?.trim())
  }
  const blockedBySelf = selected.some(([id]) => overCapacity(id) && (!isAdmin || !overrideReady(id)))

  const createBatch = async () => {
    setError('')
    if (!selected.length) { setError('Add at least one farm with a bag count.'); return }
    try {
      setBusy(true)
      const body = {
        buyer,
        season,
        created_by: getUser()?.name || '',
        contributions: selected.map(([farm_id, v]) => {
          const contrib = { farm_id: Number(farm_id), bags: Number(v), kg_per_bag: KG_PER_BAG }
          if (overCapacity(farm_id)) {
            const o = overrides[farm_id] || {}
            Object.assign(contrib, {
              override_reason: o.reason, override_note: o.note,
              overridden_by: getUser()?.name || '', overridden_by_role: getUser()?.role,
            })
          }
          return contrib
        }),
      }
      const batch = await api.createBatch(body)
      nav(`/batches/${batch.id}`)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <div className="crumbs">Workspace / Batches</div>
      <div className="page-head">
        <div>
          <h1>Shipment batches.</h1>
          <p className="muted">Bundle verified farms into a traceable shipment for your buyer.</p>
        </div>
        <button className="btn primary" onClick={() => setCreating((c) => !c)}>
          {creating ? 'Cancel' : '+ New batch'}
        </button>
      </div>

      {creating && (
        <section className="card pad">
          <b>New batch</b>
          <div className="row gap" style={{ marginTop: 8 }}>
            <div style={{ flex: 1 }}>
              <label className="field-label">Buyer</label>
              <input value={buyer} onChange={(e) => setBuyer(e.target.value)} />
            </div>
            <div>
              <label className="field-label">Season</label>
              <input value={season} onChange={(e) => setSeason(e.target.value)} style={{ width: 110 }} />
            </div>
          </div>
          <table className="table batch-select-table" style={{ marginTop: 12 }}>
            <thead>
              <tr><th></th><th>Farm / farmer</th><th>Risk</th><th>Area</th><th>Season capacity left</th><th>Bags (64 kg each)</th></tr>
            </thead>
            <tbody>
              {farms.map((f) => {
                const reason = disabledReason(f)
                const checked = bags[f.id] != null
                const over = checked && overCapacity(f.id)
                return (
                  <tr key={f.id}>
                    <td data-label="Include"><input type="checkbox" disabled={!!reason} checked={checked} onChange={() => toggleFarm(f.id)} /></td>
                    <td data-label="Farm">
                      <b>{f.name}</b><br /><small className="muted">{f.code} · {f.farmer_name}</small>
                    </td>
                    <td data-label="Risk">
                      {reason ? <span className="small muted">{reason}</span> : <RiskBadge level={f.risk_level} />}
                      {f.status === 'Verified (manual review)' && <div><span className="pill ok small">✓ Manually cleared</span></div>}
                    </td>
                    <td data-label="Area">
                      {f.capture_method === 'gps_point' ? `Point (declared ${f.area_ha.toFixed(2)} ha)` : `${f.area_ha} ha`}
                    </td>
                    <td data-label="Capacity">
                      {f.remaining_capacity_kg != null
                        ? `${Math.max(f.remaining_capacity_kg, 0).toLocaleString()} of ${f.capacity_kg.toLocaleString()} kg left`
                        : '–'}
                    </td>
                    <td data-label="Bags">
                      {checked && (
                        <>
                          <input type="number" min="1" style={{ width: 70 }} value={bags[f.id]}
                            onChange={(e) => setBagCount(f.id, e.target.value)} />
                          {over && (
                            <div className="stack" style={{ gap: 6, marginTop: 8, maxWidth: 320 }}>
                              <p className="small bad-text" style={{ margin: 0 }}>
                                ⚠ {kgFor(f.id).toLocaleString()} kg exceeds the {Math.max(f.remaining_capacity_kg, 0).toLocaleString()} kg
                                left this season -- a mass-balance override is required.
                              </p>
                              {isAdmin ? (
                                <>
                                  <select value={overrides[f.id]?.reason || ''}
                                    onChange={(e) => setOverride(f.id, { reason: e.target.value })}>
                                    <option value="">Override reason…</option>
                                    {OVERRIDE_REASONS.map((r) => <option key={r} value={r}>{r}</option>)}
                                  </select>
                                  <textarea rows={2} placeholder="Note (required)"
                                    value={overrides[f.id]?.note || ''}
                                    onChange={(e) => setOverride(f.id, { note: e.target.value })} />
                                </>
                              ) : (
                                <p className="small notice" style={{ margin: 0 }}>Ask an admin to approve.</p>
                              )}
                            </div>
                          )}
                        </>
                      )}
                    </td>
                  </tr>
                )
              })}
              {!farms.length && <tr><td colSpan="6" className="muted center">No farms registered yet.</td></tr>}
            </tbody>
          </table>
          <div className="row between" style={{ marginTop: 12 }}>
            <span className="small muted">{selected.length} farm(s) · {totalBags} bags · {totalBags * KG_PER_BAG} kg</span>
            <button className="btn primary" disabled={busy || blockedBySelf} onClick={createBatch}>
              {busy ? 'Creating…' : 'Create batch →'}
            </button>
          </div>
          {error && <p className="small bad-text">{error}</p>}
        </section>
      )}

      <section className="card">
        <div className="card-head"><b>Batches <span className="pill">{batches.length}</span></b></div>
        <table className="table batch-list-table">
          <thead><tr><th>Batch</th><th>Buyer</th><th>Season</th><th>Farms</th><th>Bags</th><th>Weight</th><th>Created</th></tr></thead>
          <tbody>
            {batches.map((b) => (
              <tr key={b.id}>
                <td data-label="Batch"><Link to={`/batches/${b.id}`}><b>{b.code}</b></Link></td>
                <td data-label="Buyer">{b.buyer}</td>
                <td data-label="Season">{b.season}</td>
                <td data-label="Farms">{b.totals.farms}</td>
                <td data-label="Bags">{b.totals.bags}</td>
                <td data-label="Weight">{b.totals.kg.toFixed(0)} kg</td>
                <td data-label="Created">{new Date(b.created_at).toLocaleDateString()}</td>
              </tr>
            ))}
            {!batches.length && <tr><td colSpan="7" className="muted center">No batches yet.</td></tr>}
          </tbody>
        </table>
      </section>
    </div>
  )
}
