import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import QRCode from 'qrcode'
import { api } from '../api.js'
import RiskBadge from '../components/RiskBadge.jsx'
import BatchMap from '../components/BatchMap.jsx'

// HLR-02 / DEL-03 / DEL-06 – batch detail: contributing farms, combined map, QR traceability link
export default function BatchDetail() {
  const { id } = useParams()
  const [batch, setBatch] = useState(null)
  const [error, setError] = useState('')
  const [qr, setQr] = useState('')

  useEffect(() => { api.batch(id).then(setBatch).catch((e) => setError(e.message)) }, [id])

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

  return (
    <div>
      <div className="crumbs"><Link to="/batches">Batches</Link> / {batch.code}</div>
      <div className="page-head">
        <div>
          <h1>{batch.code}</h1>
          <p className="muted">
            Buyer: {batch.buyer} · Created {new Date(batch.created_at).toLocaleString()}
            {batch.created_by ? ` by ${batch.created_by}` : ''}
          </p>
        </div>
        <div className="row gap">
          <a className="btn" href={api.bagLabelsUrl(batch.id)}>🏷 Print bag labels</a>
          <a className="btn primary" href={api.batchReportUrl(batch.id)}>⤓ Export shipment compliance package</a>
        </div>
      </div>

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
          <p className="small muted">Scan to open this batch's record.</p>
          {qr && <img src={qr} alt="Batch QR code" width={200} height={200} />}
        </aside>
      </div>

      <section className="card">
        <div className="card-head"><b>Contributing farms</b></div>
        <table className="table contrib-table">
          <thead><tr><th>Farm / farmer</th><th>Risk</th><th>Area</th><th>Bags</th><th>Weight</th></tr></thead>
          <tbody>
            {batch.contributions.map((c) => (
              <tr key={c.farm?.id ?? Math.random()}>
                <td data-label="Farm">
                  {c.farm ? (
                    <>
                      <Link to={`/farms/${c.farm.id}`}><b>{c.farm.name}</b></Link><br />
                      <small className="muted">{c.farm.code} · {c.farm.farmer_name}</small>
                      {c.farm.status === 'Verified (manual review)' && (
                        <div><span className="pill ok small">✓ Manually cleared</span></div>
                      )}
                    </>
                  ) : <span className="muted">Farm removed</span>}
                </td>
                <td data-label="Risk">{c.farm ? <RiskBadge level={c.farm.risk_level} /> : '–'}</td>
                <td data-label="Area">{c.farm ? `${c.farm.area_ha} ha` : '–'}</td>
                <td data-label="Bags">{c.bags}</td>
                <td data-label="Weight">{c.kg.toFixed(0)} kg</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <p className="disclaimer small">
        Satellite screening is decision support, not EUDR certification or a legal determination. Small-scale change and
        agroforestry may be missed. Confirm land rights, legality and supply-chain evidence before due-diligence approval.
      </p>
    </div>
  )
}
