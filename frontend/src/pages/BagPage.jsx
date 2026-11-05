import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../api.js'
import FarmMap from '../components/FarmMap.jsx'

const VERIFIED_STATUSES = ['Verified', 'Verified (manual review)']

// Public, no-login bag page -- the destination of every bag label's QR code. Mobile-friendly.
export default function BagPage() {
  const { id } = useParams()
  const [bag, setBag] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => { api.bag(id).then(setBag).catch((e) => setError(e.message)) }, [id])

  if (error) {
    return (
      <div className="bag-page">
        <p className="bad-text center">{error === 'Bag not found' ? 'Bag not found.' : error}</p>
      </div>
    )
  }
  if (!bag) return <div className="bag-page"><p className="muted center">Loading…</p></div>

  const farm = bag.farm
  const verified = farm && VERIFIED_STATUSES.includes(farm.status)
  const verifiedAt = farm?.verification?.created_at || farm?.last_screened

  return (
    <div className="bag-page">
      <div className="bag-brand">❦ COCOA INTELLIGENCE</div>

      <div className={`bag-status ${verified ? 'ok' : 'bad'}`}>
        <span className="icon">{verified ? '✓' : '✗'}</span>
        <span className="label">
          {verified ? 'Deforestation-free verified' : `Not verified — ${farm?.status || 'unknown'}`}
        </span>
      </div>

      <div className="card pad">
        <div className="bag-row"><span>Bag ID</span><b>{bag.bag_id}</b></div>
        <div className="bag-row"><span>Weight</span><b>{bag.weight_kg.toFixed(0)} kg</b></div>
        <div className="bag-row"><span>Farm</span><b>{farm?.name || '–'}</b></div>
        <div className="bag-row"><span>Region</span><b>{farm?.village || farm?.region || '–'}</b></div>
        <div className="bag-row">
          <span>Verification date</span>
          <b>{verifiedAt ? new Date(verifiedAt).toLocaleDateString() : 'Not yet screened'}</b>
        </div>
        <div className="bag-row"><span>Batch</span><b>{bag.batch?.code || '–'}</b></div>
        <div className="bag-row"><span>Buyer</span><b>{bag.batch?.buyer || '–'}</b></div>
      </div>

      {farm?.geometry && (
        <div className="card" style={{ marginTop: 14 }}>
          <FarmMap
            geometry={farm.geometry}
            boundaryStyle={{ color: '#ffe034', weight: 3, fillColor: '#ffe034', fillOpacity: 0.12 }}
            height={220}
          />
        </div>
      )}

      <p className="muted small center" style={{ marginTop: 16 }}>
        Satellite screening is decision support, not EUDR certification or a legal determination.
      </p>
    </div>
  )
}
