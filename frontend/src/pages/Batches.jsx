import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api.js'
import { getUser } from '../App.jsx'
import RiskBadge from '../components/RiskBadge.jsx'

// HLR-02 / DEL-03 / DEL-06 – bundle verified farms into a traceable shipment batch
const DEFAULT_BUYER = 'Chocolaterie Vandermeer, Ghent, Belgium'
const KG_PER_BAG = 64

function disabledReason(farm) {
  if (farm.status === 'Verified' || farm.status === 'Verified (manual review)') return null
  if (!farm.risk_level) return 'Not screened'
  if (farm.status === 'Excluded') return `Excluded – ${farm.latest_review?.reason || 'manual review'}`
  return `${farm.risk_level} risk – under review`
}

export default function Batches() {
  const nav = useNavigate()
  const [batches, setBatches] = useState([])
  const [farms, setFarms] = useState([])
  const [creating, setCreating] = useState(false)
  const [buyer, setBuyer] = useState(DEFAULT_BUYER)
  const [bags, setBags] = useState({}) // farmId -> bag count string
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const load = () => Promise.all([api.batches(), api.farms()]).then(([b, f]) => { setBatches(b); setFarms(f) })
  useEffect(() => { load() }, [])

  const toggleFarm = (id) => {
    setBags((b) => {
      const next = { ...b }
      if (next[id] != null) delete next[id]
      else next[id] = '10'
      return next
    })
  }
  const setBagCount = (id, v) => setBags((b) => ({ ...b, [id]: v }))

  const selected = Object.entries(bags).filter(([, v]) => Number(v) > 0)
  const totalBags = selected.reduce((s, [, v]) => s + Number(v), 0)

  const createBatch = async () => {
    setError('')
    if (!selected.length) { setError('Add at least one farm with a bag count.'); return }
    try {
      setBusy(true)
      const body = {
        buyer,
        created_by: getUser()?.name || '',
        contributions: selected.map(([farm_id, v]) => (
          { farm_id: Number(farm_id), bags: Number(v), kg_per_bag: KG_PER_BAG }
        )),
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
          <label className="field-label">Buyer</label>
          <input value={buyer} onChange={(e) => setBuyer(e.target.value)} />
          <table className="table batch-select-table" style={{ marginTop: 12 }}>
            <thead>
              <tr><th></th><th>Farm / farmer</th><th>Risk</th><th>Area</th><th>Bags (64 kg each)</th></tr>
            </thead>
            <tbody>
              {farms.map((f) => {
                const reason = disabledReason(f)
                const checked = bags[f.id] != null
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
                    <td data-label="Area">{f.area_ha} ha</td>
                    <td data-label="Bags">
                      {checked && (
                        <input type="number" min="1" style={{ width: 70 }} value={bags[f.id]}
                          onChange={(e) => setBagCount(f.id, e.target.value)} />
                      )}
                    </td>
                  </tr>
                )
              })}
              {!farms.length && <tr><td colSpan="5" className="muted center">No farms registered yet.</td></tr>}
            </tbody>
          </table>
          <div className="row between" style={{ marginTop: 12 }}>
            <span className="small muted">{selected.length} farm(s) · {totalBags} bags · {totalBags * KG_PER_BAG} kg</span>
            <button className="btn primary" disabled={busy} onClick={createBatch}>
              {busy ? 'Creating…' : 'Create batch →'}
            </button>
          </div>
          {error && <p className="small bad-text">{error}</p>}
        </section>
      )}

      <section className="card">
        <div className="card-head"><b>Batches <span className="pill">{batches.length}</span></b></div>
        <table className="table batch-list-table">
          <thead><tr><th>Batch</th><th>Buyer</th><th>Farms</th><th>Bags</th><th>Weight</th><th>Created</th></tr></thead>
          <tbody>
            {batches.map((b) => (
              <tr key={b.id}>
                <td data-label="Batch"><Link to={`/batches/${b.id}`}><b>{b.code}</b></Link></td>
                <td data-label="Buyer">{b.buyer}</td>
                <td data-label="Farms">{b.totals.farms}</td>
                <td data-label="Bags">{b.totals.bags}</td>
                <td data-label="Weight">{b.totals.kg.toFixed(0)} kg</td>
                <td data-label="Created">{new Date(b.created_at).toLocaleDateString()}</td>
              </tr>
            ))}
            {!batches.length && <tr><td colSpan="6" className="muted center">No batches yet.</td></tr>}
          </tbody>
        </table>
      </section>
    </div>
  )
}
