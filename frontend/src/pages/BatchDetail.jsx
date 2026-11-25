import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import QRCode from 'qrcode'
import { api } from '../api.js'
import { getUser } from '../App.jsx'
import RiskBadge from '../components/RiskBadge.jsx'
import BatchMap from '../components/BatchMap.jsx'

// HLR-02 / DEL-03 / DEL-06 – batch detail: contributing farms, combined map, QR traceability link
// Mass-balance approval workflow (issue #15): a Pending approval batch shows a banner instead of
// exports, and a Brand admin reviews/approves/rejects its over-capacity lines right here.
const STATUS_PILL = { Approved: 'ok', 'Pending approval': 'warn', Rejected: 'bad', Cancelled: '' }
const OVERRIDE_REASONS = ['Second harvest', 'Yield above average', 'Data correction', 'Other']
const EVENT_LABELS = {
  submitted: 'Submitted for approval', approved: 'Approved', rejected: 'Rejected', cancelled: 'Cancelled',
}

export default function BatchDetail() {
  const { id } = useParams()
  const isAdmin = getUser()?.role === 'admin'
  const [batch, setBatch] = useState(null)
  const [error, setError] = useState('')
  const [qr, setQr] = useState('')
  const [overrides, setOverrides] = useState({}) // contributionId -> { reason, note }
  const [approveNote, setApproveNote] = useState('')
  const [rejectReason, setRejectReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [decisionError, setDecisionError] = useState('')

  const load = () => api.batch(id).then(setBatch).catch((e) => setError(e.message))
  useEffect(() => { load() }, [id]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!batch) return
    const url = `${window.location.origin}/batches/${batch.id}`
    QRCode.toDataURL(url, { margin: 1, width: 240 }).then(setQr).catch(() => {})
  }, [batch])

  if (error && !batch) return <p className="bad-text">{error}</p>
  if (!batch) return <p className="muted">Loading…</p>

  const farmsForMap = batch.contributions
    .filter((c) => c.farm)
    .map((c) => ({ id: c.farm.id, name: c.farm.name, geometry: c.farm.geometry }))
  const pending = batch.status === 'Pending approval'
  const approved = batch.status === 'Approved'
  const overCapacityLines = batch.contributions.filter((c) => c.capacity?.over_capacity)
  const setOverride = (cid, patch) => setOverrides((o) => ({ ...o, [cid]: { ...o[cid], ...patch } }))
  const overrideReady = (c) => Boolean(overrides[c.id]?.reason && overrides[c.id]?.note?.trim())
  const canApprove = overCapacityLines.every(overrideReady)

  const refresh = async (action) => {
    setDecisionError('')
    setBusy(true)
    try {
      await action()
      await load()
    } catch (e) {
      setDecisionError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const approve = () => refresh(() => api.approveBatch(batch.id, {
    approved_by: getUser()?.name || '', approved_by_role: getUser()?.role, note: approveNote,
    overrides: overCapacityLines.map((c) => ({
      contribution_id: c.id, override_reason: overrides[c.id]?.reason, override_note: overrides[c.id]?.note,
    })),
  }))

  const reject = () => {
    if (!rejectReason.trim()) { setDecisionError('A rejection reason is required.'); return }
    return refresh(() => api.rejectBatch(batch.id, {
      by: getUser()?.name || '', by_role: getUser()?.role, reason: rejectReason,
    }))
  }

  const cancel = () => refresh(() => api.cancelBatch(batch.id, {
    by: getUser()?.name || '', by_role: getUser()?.role,
  }))

  return (
    <div>
      <div className="crumbs"><Link to="/batches">Batches</Link> / {batch.code}</div>
      <div className="page-head">
        <div>
          <h1>{batch.code} <span className={`pill ${STATUS_PILL[batch.status] || ''}`} style={{ marginLeft: 8, verticalAlign: 'middle' }}>{batch.status}</span></h1>
          <p className="muted">
            Buyer: {batch.buyer} · Season {batch.season} · Created {new Date(batch.created_at).toLocaleString()}
            {batch.created_by ? ` by ${batch.created_by}` : ''}
          </p>
        </div>
        {approved && (
          <div className="row gap page-actions">
            <a className="btn" href={api.bagLabelsUrl(batch.id)}>🏷 Print bag labels</a>
            <a className="btn" href={api.geolocationUrl(batch.id)}>⬠ Export EUDR geolocation (GeoJSON)</a>
            <a className="btn primary" href={api.batchReportUrl(batch.id)}>⤓ Export shipment compliance package</a>
          </div>
        )}
      </div>

      {pending && (
        <div className="notice">
          <p style={{ margin: 0 }}>
            <b>Pending approval.</b> This batch exceeds one or more farms' remaining season
            capacity and is awaiting a Brand admin's decision. No bags, labels, QR code, PDF or
            GeoJSON exist until it's approved.
          </p>
          {batch.submission_note && (
            <p className="small" style={{ margin: '6px 0 0' }}>Justification: {batch.submission_note}</p>
          )}
          {!isAdmin && (
            <div style={{ marginTop: 8 }}>
              <button className="btn" disabled={busy} onClick={cancel}>Cancel request</button>
            </div>
          )}
        </div>
      )}
      {batch.status === 'Rejected' && (
        <div className="notice"><b>Rejected.</b> This batch was rejected and will not ship.</div>
      )}
      {batch.status === 'Cancelled' && (
        <div className="notice"><b>Cancelled.</b> This request was withdrawn before a decision.</div>
      )}

      {pending && isAdmin && (
        <section className="card pad">
          <b>Review for approval</b>
          {overCapacityLines.length > 0 ? (
            <div className="stack" style={{ gap: 10, marginTop: 10 }}>
              {overCapacityLines.map((c) => (
                <div key={c.id} className="card pad" style={{ borderColor: '#ecd58f' }}>
                  <p className="small" style={{ margin: 0 }}>
                    <b>{c.farm?.name}</b> — requested <b>{c.capacity.requested_kg.toLocaleString()} kg</b>,
                    {' '}{Math.max(c.capacity.remaining_kg, 0).toLocaleString()} kg remaining,
                    {' '}overage <b className="bad-text">{c.capacity.overage_kg.toLocaleString()} kg</b>
                  </p>
                  <div className="row gap" style={{ marginTop: 6, flexWrap: 'wrap' }}>
                    <select value={overrides[c.id]?.reason || ''} onChange={(e) => setOverride(c.id, { reason: e.target.value })}>
                      <option value="">Override reason…</option>
                      {OVERRIDE_REASONS.map((r) => <option key={r} value={r}>{r}</option>)}
                    </select>
                    <input style={{ flex: 1, minWidth: 200 }} placeholder="Note (required)"
                      value={overrides[c.id]?.note || ''} onChange={(e) => setOverride(c.id, { note: e.target.value })} />
                  </div>
                </div>
              ))}
            </div>
          ) : <p className="small muted">No over-capacity lines.</p>}
          <label className="field-label" style={{ marginTop: 10 }}>Approval note (optional)</label>
          <textarea rows={2} value={approveNote} onChange={(e) => setApproveNote(e.target.value)} />
          <div className="row gap" style={{ marginTop: 10 }}>
            <button className="btn primary" disabled={busy || !canApprove} onClick={approve}>
              {busy ? 'Approving…' : 'Approve →'}
            </button>
          </div>
          <div style={{ marginTop: 14, borderTop: '1px solid #e6dfd2', paddingTop: 10 }}>
            <label className="field-label">Rejection reason (required to reject)</label>
            <textarea rows={2} value={rejectReason} onChange={(e) => setRejectReason(e.target.value)} />
            <button className="btn" style={{ marginTop: 8 }} disabled={busy} onClick={reject}>
              {busy ? 'Rejecting…' : 'Reject'}
            </button>
          </div>
          {decisionError && <p className="small bad-text">{decisionError}</p>}
        </section>
      )}

      <div className="kpis">
        <div className="card pad"><small className="muted">Contributing farms</small><b>{batch.totals.farms}</b></div>
        <div className="card pad"><small className="muted">Total bags</small><b>{batch.totals.bags}</b></div>
        <div className="card pad"><small className="muted">Total weight</small><b>{batch.totals.kg.toFixed(0)} <small>kg</small></b></div>
        <div className="card pad"><small className="muted">Mapped area</small><b>{batch.totals.area_ha} <small>ha</small></b></div>
      </div>

      <div className="grid-result">
        <section className="card">
          <div className="card-head"><b>Farm boundaries</b></div>
          <BatchMap farms={farmsForMap} height={400} />
          <div className="card-foot small muted">Click a farm boundary to open its result page.</div>
        </section>

        <aside className="card pad center">
          <b>Traceability QR</b>
          {approved ? (
            <>
              <p className="small muted">Scan to open this batch's record.</p>
              {qr && <img src={qr} alt="Batch QR code" width={200} height={200} />}
            </>
          ) : (
            <p className="small muted">The QR code appears once this batch is approved.</p>
          )}
        </aside>
      </div>

      <section className="card">
        <div className="card-head"><b>Contributing farms</b></div>
        <table className="table contrib-table">
          <thead><tr><th>Farm / farmer</th><th>Risk</th><th>Area</th><th>Bags</th><th>Weight</th></tr></thead>
          <tbody>
            {batch.contributions.map((c) => (
              <tr key={c.id ?? c.farm?.id ?? Math.random()}>
                <td data-label="Farm">
                  {c.farm ? (
                    <>
                      <Link to={`/farms/${c.farm.id}`}><b>{c.farm.name}</b></Link><br />
                      <small className="muted">{c.farm.code} · {c.farm.farmer_name}</small>
                      {c.farm.status === 'Verified (manual review)' && (
                        <div><span className="pill ok small">✓ Manually cleared</span></div>
                      )}
                      {c.override && (
                        <div className="small" style={{ color: '#b8860b' }}>
                          ⚠ Mass-balance override: {c.override.reason} — {c.override.by || 'n/a'}
                          {c.override.at ? ` (${new Date(c.override.at).toLocaleDateString()})` : ''}
                        </div>
                      )}
                      {c.capacity?.over_capacity && (
                        <div className="small bad-text">
                          ⚠ {c.capacity.overage_kg.toLocaleString()} kg over the {Math.max(c.capacity.remaining_kg, 0).toLocaleString()} kg remaining
                        </div>
                      )}
                    </>
                  ) : <span className="muted">Farm removed</span>}
                </td>
                <td data-label="Risk">{c.farm ? <RiskBadge level={c.farm.risk_level} /> : '–'}</td>
                <td data-label="Area">
                  {c.farm
                    ? c.farm.capture_method === 'gps_point'
                      ? `Point (declared ${c.farm.area_ha.toFixed(2)} ha)`
                      : `${c.farm.area_ha} ha`
                    : '–'}
                </td>
                <td data-label="Bags">{c.bags}</td>
                <td data-label="Weight">{c.kg.toFixed(0)} kg</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {batch.events?.length > 0 && (
        <section className="card">
          <div className="card-head"><b>Approval history</b></div>
          <table className="table">
            <thead><tr><th>Event</th><th>By</th><th>Date</th><th>Note</th></tr></thead>
            <tbody>
              {batch.events.map((e, i) => (
                <tr key={i}>
                  <td data-label="Event">{EVENT_LABELS[e.event] || e.event}</td>
                  <td data-label="By">{e.by || '–'}</td>
                  <td data-label="Date">{new Date(e.created_at).toLocaleString()}</td>
                  <td data-label="Note">{e.note || '–'}</td>
                </tr>
              ))}
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
