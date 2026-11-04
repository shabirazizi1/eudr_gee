import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import area from '@turf/area'
import length from '@turf/length'
import kinks from '@turf/kinks'
import { api, queueFarm } from '../api.js'
import { getUser } from '../App.jsx'
import FarmMap from '../components/FarmMap.jsx'

// F2 – Farm registry & GPS boundary capture
const DRAFT_KEY = 'eudr_capture_draft'
const EMPTY = { name: '', farmer_name: '', village: '', harvest_date: '' }

function loadDraft() {
  try { return JSON.parse(localStorage.getItem(DRAFT_KEY)) } catch { return null }
}

export default function Capture() {
  const nav = useNavigate()
  const draft = loadDraft()
  const [form, setForm] = useState(draft?.form || EMPTY)
  const [geometry, setGeometry] = useState(draft?.geometry || null)
  const [savedAt, setSavedAt] = useState(draft?.savedAt || null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  // Auto-save draft locally on every change (works with no signal)
  useEffect(() => {
    const t = new Date().toISOString()
    try { localStorage.setItem(DRAFT_KEY, JSON.stringify({ form, geometry, savedAt: t })) } catch { /* ignore */ }
    setSavedAt(t)
  }, [form, geometry])

  const stats = useMemo(() => {
    if (!geometry) return null
    const f = { type: 'Feature', geometry, properties: {} }
    const ring = geometry.coordinates[0]
    return {
      ha: area(f) / 10000,
      perimeter: length({ type: 'Feature', geometry: { type: 'LineString', coordinates: ring } }) * 1000,
      vertices: ring.length - 1,
      valid: kinks(f).features.length === 0,
      points: ring.slice(0, -1),
    }
  }, [geometry])

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value })
  const canSubmit = form.name && form.farmer_name && stats?.valid && stats.vertices >= 3

  const clearDraft = () => {
    localStorage.removeItem(DRAFT_KEY)
    setForm(EMPTY)
    setGeometry(null)
  }

  const submit = async (runCheck) => {
    setError(''); setNotice('')
    const body = { ...form, harvest_date: form.harvest_date || null, geometry, captured_by: getUser()?.name || '' }
    if (!navigator.onLine) {
      queueFarm(body)
      clearDraft()
      setNotice('No signal: farm saved on this device and will sync automatically when you are back online.')
      return
    }
    try {
      setBusy('Saving farm…')
      const farm = await api.createFarm(body)
      clearDraft()
      if (runCheck) {
        setBusy('Running satellite check (can take 10–40 s)…')
        await api.verify(farm.id)
      }
      nav(`/farms/${farm.id}`)
    } catch (e) {
      if (e.status) setError(e.message)
      else {
        queueFarm(body)
        clearDraft()
        setNotice('Server unreachable: farm saved on this device and will sync later.')
      }
    } finally {
      setBusy('')
    }
  }

  return (
    <div>
      <div className="crumbs">Farm registry / New farm / Boundary capture</div>
      <div className="page-head">
        <div>
          <h1>Map the farm. Ground the evidence.</h1>
          <p className="muted">Capture a precise boundary before satellite screening.</p>
        </div>
      </div>

      <div className="grid-capture">
        <section className="card">
          <div className="card-head"><b>Boundary editor</b>
            <span className={`pill ${stats?.valid ? 'ok' : ''}`}>
              {stats ? (stats.valid ? '● Polygon closed' : '● Fix crossing lines') : 'Draw the polygon ⬠'}
            </span>
          </div>
          <FarmMap editable geometry={geometry} onChange={setGeometry} />
          <div className="card-foot muted small">
            Use the polygon tool (⬠) on the left. Follow the outer cultivated edge, exclude roads and
            neighbouring plots, and confirm the boundary with the farmer.
          </div>
        </section>

        <aside className="stack">
          <section className="card pad">
            <b>Farm details</b>
            <label className="field-label">Farm name *</label>
            <input value={form.name} onChange={set('name')} placeholder="Adom Cocoa Farm" />
            <label className="field-label">Farmer name *</label>
            <input value={form.farmer_name} onChange={set('farmer_name')} placeholder="Ama Mensah" />
            <label className="field-label">Village / community</label>
            <input value={form.village} onChange={set('village')} placeholder="Asankrangwa" />
            <label className="field-label">Last harvest date</label>
            <input type="date" value={form.harvest_date} onChange={set('harvest_date')} />
          </section>

          <section className="card pad">
            <div className="card-head tight"><b>Captured boundary</b><small className="muted">WGS 84</small></div>
            <div className="stats3">
              <div><b>{stats ? stats.ha.toFixed(2) : '–'}</b><small>hectares</small></div>
              <div><b>{stats ? Math.round(stats.perimeter) : '–'}</b><small>m perimeter</small></div>
              <div><b>{stats ? stats.vertices : '–'}</b><small>vertices</small></div>
            </div>
            {stats && (
              <>
                <table className="coords">
                  <thead><tr><th>#</th><th>Latitude</th><th>Longitude</th></tr></thead>
                  <tbody>
                    {stats.points.slice(0, 8).map(([lon, lat], i) => (
                      <tr key={i}><td>{String(i + 1).padStart(2, '0')}</td><td>{lat.toFixed(5)}</td><td>{lon.toFixed(5)}</td></tr>
                    ))}
                  </tbody>
                </table>
                <p className={`small ${stats.valid ? 'ok-text' : 'bad-text'}`}>
                  {stats.valid ? '✓ Valid geometry · no self-intersections' : '✗ Boundary crosses itself'}
                </p>
                {stats.ha > 50 && <p className="small bad-text">⚠ Unusually large for a smallholder farm.</p>}
              </>
            )}
            {error && <p className="small bad-text">{error}</p>}
            {notice && <p className="small notice">{notice}</p>}
            <button className="btn primary wide" disabled={!canSubmit || !!busy} onClick={() => submit(true)}>
              {busy || 'Save & run satellite check →'}
            </button>
            <button className="btn wide" disabled={!canSubmit || !!busy} onClick={() => submit(false)}>Save only</button>
            <button className="link-btn small" onClick={clearDraft}>Clear draft</button>
          </section>
          <p className="muted small">Draft saved locally {savedAt ? new Date(savedAt).toLocaleTimeString() : ''}</p>
        </aside>
      </div>
    </div>
  )
}
