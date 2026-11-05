import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api.js'
import { getUser } from '../App.jsx'
import FarmMap from '../components/FarmMap.jsx'
import RiskBadge from '../components/RiskBadge.jsx'

// F3 – GEE satellite verification result (Figure 3)
// HLR-05 / DEL-02 – admin review / dispute workflow for farms Under review
const REVIEW_REASONS = ['Cloud cover', 'Natural clearing', 'Replanting of existing cocoa', 'Boundary error', 'Other']

function ReviewPanel({ farm, isAdmin, onReviewed }) {
  const [decision, setDecision] = useState(null) // 'clear' | 'confirm' | null
  const [reason, setReason] = useState(REVIEW_REASONS[0])
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const pick = (d) => { setDecision(d); setError('') }

  const submit = async () => {
    if (!note.trim()) { setError('A note is required.'); return }
    setError(''); setBusy(true)
    try {
      const updated = await api.reviewFarm(farm.id, {
        decision, reason, note: note.trim(), reviewer_name: getUser()?.name || '',
      })
      onReviewed(updated)
      setDecision(null); setNote('')
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card pad">
      <div className="card-head tight"><b>Review decision</b><small className="muted">Brand admin</small></div>
      {!isAdmin && (
        <p className="small muted">Read-only — only a Brand admin can record a review decision.</p>
      )}
      <div className="row gap">
        <button className="btn" disabled={!isAdmin} onClick={() => pick('clear')}>Clear as false positive</button>
        <button className="btn" disabled={!isAdmin} onClick={() => pick('confirm')}>Confirm risk – exclude from supply</button>
      </div>
      {isAdmin && decision && (
        <div className="stack" style={{ gap: 8, marginTop: 10 }}>
          <label className="field-label">Reason</label>
          <select value={reason} onChange={(e) => setReason(e.target.value)}>
            {REVIEW_REASONS.map((r) => <option key={r}>{r}</option>)}
          </select>
          <label className="field-label">Note *</label>
          <textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)}
            placeholder="What did you check, and why this decision?" />
          {error && <p className="small bad-text">{error}</p>}
          <div className="row gap">
            <button className="btn primary" disabled={busy} onClick={submit}>
              {busy ? 'Saving…' : decision === 'clear' ? 'Confirm: clear as false positive' : 'Confirm: exclude from supply'}
            </button>
            <button className="btn" disabled={busy} onClick={() => { setDecision(null); setError('') }}>Cancel</button>
          </div>
        </div>
      )}
    </section>
  )
}

function ReviewHistory({ reviews }) {
  return (
    <section className="card pad">
      <div className="card-head tight"><b>Review history</b><small className="muted">{reviews.length} decision(s)</small></div>
      <div className="stack" style={{ gap: 10 }}>
        {reviews.map((r) => (
          <div key={r.id} style={{ borderBottom: '1px dashed var(--line)', paddingBottom: 8 }}>
            <div className="row between">
              <b className={r.decision === 'clear' ? 'ok-text' : 'bad-text'}>
                {r.decision === 'clear' ? '✓ Cleared — false positive' : '✗ Confirmed — excluded from supply'}
              </b>
              <span className="small muted">{new Date(r.created_at).toLocaleString()}</span>
            </div>
            <div className="small">{r.reason}{r.reviewer_name ? ` · ${r.reviewer_name}` : ''}</div>
            {r.note && <div className="small muted">{r.note}</div>}
          </div>
        ))}
      </div>
    </section>
  )
}

export default function Result() {
  const { id } = useParams()
  const [farm, setFarm] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const isAdmin = getUser()?.role === 'admin'

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
          <p className="muted">
            {farm.name} · {farm.code} · {farm.farmer_name} · {farm.village || farm.region}
            {farm.needs_field_audit && <> · <span className="pill warn small">⚠ Needs field audit</span></>}
          </p>
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
        <section className="card result-map">
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

        <aside className="card pad result-score">
          <div className="card-head tight"><b>Satellite risk score</b><small className="muted">GEE screening</small></div>
          {v ? (
            <>
              <div className="score"><span className="num">{v.score}</span>/100 <RiskBadge level={v.level} /></div>
              <div className="scale"><i style={{ left: `${v.score}%` }} /></div>
              <div className="row between small muted"><span>0 · Lower risk</span><span>100 · Higher risk</span></div>
              <ul className="facts">
                <li><span>Capture method</span><b>{farm.capture_method === 'gps_walk' ? 'GPS walk' : 'Drawn on map'}</b></li>
                <li><span>GPS accuracy</span><b>
                  {farm.capture_method === 'gps_walk' && farm.gps_accuracy_m != null
                    ? `±${farm.gps_accuracy_m.toFixed(1)} m avg${farm.flagged_points ? ` · ${farm.flagged_points} flagged` : ''}`
                    : '–'}
                </b></li>
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

      {farm.status === 'Under review' && (
        <ReviewPanel farm={farm} isAdmin={isAdmin} onReviewed={setFarm} />
      )}
      {farm.reviews?.length > 0 && <ReviewHistory reviews={farm.reviews} />}

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
