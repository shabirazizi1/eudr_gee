import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api.js'
import FarmMap from '../components/FarmMap.jsx'
import RiskBadge from '../components/RiskBadge.jsx'

// F3 – GEE satellite verification result (Figure 3)
export default function Result() {
  const { id } = useParams()
  const [farm, setFarm] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => { api.farm(id).then(setFarm).catch((e) => setError(e.message)) }, [id])

  const run = async () => {
    setBusy(true); setError('')
    try { setFarm(await api.verify(id)) } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  if (error && !farm) return <p className="bad-text">{error}</p>
  if (!farm) return <p className="muted">Loading…</p>
  const v = farm.verification

  return (
    <div>
      <div className="crumbs"><Link to="/farms">Satellite verification</Link> / {farm.code} / Analysis result</div>
      <div className="page-head">
        <div>
          <h1>A clearer view of the farm.</h1>
          <p className="muted">{farm.name} · {farm.code} · {farm.farmer_name} · {farm.village || farm.region}</p>
        </div>
        <div className="row">
          <button className="btn" onClick={run} disabled={busy}>{busy ? 'Screening… (10–40 s)' : v ? '↻ Re-run check' : 'Run satellite check'}</button>
          {v && <a className="btn primary" href={api.reportUrl(farm.id)}>⤓ Export evidence</a>}
        </div>
      </div>
      {error && <p className="bad-text">{error}</p>}

      {v ? (
        <>
          <div className={`verdict ${v.level.toLowerCase()}`}>
            <div>
              <b>{v.level} risk · {v.loss_ha > 0 ? `${v.loss_ha.toFixed(2)} ha post-2020 forest loss detected` : 'No post-2020 forest loss detected'}</b>
              <div className="small">Status: {farm.status}{v.flags?.length ? ` · ${v.flags.length} flag(s) to review` : ''}</div>
            </div>
            <div className="small right">● Analysis complete<br />{new Date(v.created_at).toLocaleString()}</div>
          </div>
          {v.source === 'mock' && (
            <p className="notice small">
              ⚠ MOCK result – Earth Engine was not used{v.gee_error ? ` (error: ${v.gee_error})` : ''}. Do not present as real evidence.
            </p>
          )}
        </>
      ) : (
        <p className="notice">This farm hasn't been screened yet. Click “Run satellite check”.</p>
      )}

      <div className="grid-result">
        <section className="card">
          <div className="card-head"><b>Forest change screening</b>
            {v?.loss_tiles && <span className="pill"><span className="sw red" /> Tree-cover loss after 2020</span>}
          </div>
          <FarmMap
            geometry={farm.geometry}
            label={`${farm.name} · ${farm.area_ha} ha`}
            overlayUrl={v?.loss_tiles}
            boundaryStyle={{ color: '#ffe034', weight: 3, fillColor: '#ffe034', fillOpacity: 0.08 }}
            height={400}
          />
          <div className="card-foot small muted row between">
            <span>{v?.loss_tiles ? 'Red pixels = Hansen tree-cover loss after the cutoff' : 'Farm boundary'}</span>
            <span>Cut-off: 31 Dec 2020</span>
          </div>
        </section>

        <aside className="card pad">
          <div className="card-head tight"><b>Satellite risk score</b><small className="muted">GEE screening</small></div>
          {v ? (
            <>
              <div className="score"><span className="num">{v.score}</span>/100 <RiskBadge level={v.level} /></div>
              <div className="scale"><i style={{ left: `${v.score}%` }} /></div>
              <div className="row between small muted"><span>0 · Lower risk</span><span>100 · Higher risk</span></div>
              <ul className="facts">
                <li><span>Forest-loss overlap</span><b>{v.loss_ha.toFixed(2)} ha</b></li>
                <li><span>Forest cover in 2020</span><b>{v.forest_2020_ha} ha</b></li>
                <li><span>Protected-area overlap</span><b>{v.protected_areas?.length ? v.protected_areas.join(', ') : 'None detected'}</b></li>
                <li><span>Usable image coverage</span><b>{Math.round(v.coverage_pct)}% of farm</b></li>
                <li><span>Vegetation (NDVI) 2020 → now</span><b>{v.ndvi_baseline ?? '–'} → {v.ndvi_current ?? '–'}</b></li>
              </ul>
              <b className="small">Score breakdown</b>
              <ul className="facts small">
                {v.breakdown.map((b) => <li key={b.factor}><span>{b.factor}</span><b>+{b.points}</b></li>)}
              </ul>
              {v.flags?.length > 0 && (
                <ul className="flags small">{v.flags.map((f) => <li key={f}>⚠ {f}</li>)}</ul>
              )}
            </>
          ) : <p className="muted small">No result yet.</p>}
        </aside>
      </div>

      {v && (
        <section className="card">
          <div className="card-head"><b>Evidence & methodology</b><small className="muted">Run {v.run_id}</small></div>
          <table className="table">
            <thead><tr><th>Data source</th><th>Observation window</th><th>Resolution</th></tr></thead>
            <tbody>
              {v.datasets.map((d) => <tr key={d.name}><td>{d.name}</td><td>{d.window}</td><td>{d.resolution}</td></tr>)}
            </tbody>
          </table>
        </section>
      )}
      <p className="disclaimer small">
        Satellite screening is decision support, not EUDR certification or a legal determination. Small-scale change and
        agroforestry may be missed. Confirm land rights, legality and supply-chain evidence before due-diligence approval.
      </p>
    </div>
  )
}
