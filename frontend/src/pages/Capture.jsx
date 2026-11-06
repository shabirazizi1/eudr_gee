import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import area from '@turf/area'
import length from '@turf/length'
import kinks from '@turf/kinks'
import { api, queueFarm } from '../api.js'
import { getUser } from '../App.jsx'
import FarmMap from '../components/FarmMap.jsx'

// F2 – Farm registry & GPS boundary capture (HLR-01: "Walk the boundary" GPS mode)
const DRAFT_KEY = 'eudr_capture_draft'
const EMPTY = { name: '', farmer_name: '', village: '', harvest_date: '' }
const SAMPLE_MS = 3000       // how long "Add point here" averages GPS readings for
const GOOD_ACCURACY_M = 10   // charter target
const BLOCK_ACCURACY_M = 30  // worse than this, the point is rejected outright
const FRESH_READING_MS = 10000 // a live reading this old (or newer) can seed the sample window

function loadDraft() {
  try { return JSON.parse(localStorage.getItem(DRAFT_KEY)) } catch { return null }
}

const mean = (xs) => xs.reduce((s, x) => s + x, 0) / xs.length

// watchPosition doesn't fire again once the device stops moving, so a single reading on demand
// (used as a last-resort fallback when the 3 s window collects nothing) needs its own call.
function getPositionOnce() {
  return new Promise((resolve) => {
    if (!navigator.geolocation) { resolve(null); return }
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({
        lat: pos.coords.latitude, lon: pos.coords.longitude, accuracy: pos.coords.accuracy, timestamp: Date.now(),
      }),
      () => resolve(null),
      { enableHighAccuracy: true, maximumAge: 0, timeout: 10000 },
    )
  })
}

function buildGeometry(points) {
  if (points.length < 3) return null
  const ring = points.map((p) => [p.lon, p.lat])
  ring.push(ring[0])
  return { type: 'Polygon', coordinates: [ring] }
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

  // Walk-the-boundary state
  const [mode, setModeState] = useState(draft?.mode || 'draw')             // 'draw' | 'walk'
  const [walkPoints, setWalkPoints] = useState(draft?.walkPoints || [])    // [{lat, lon, accuracy, flagged}]
  const [walkFinished, setWalkFinished] = useState(draft?.walkFinished || false)
  const [captureMethod, setCaptureMethod] = useState(draft?.captureMethod || 'drawn')
  const [livePosition, setLivePosition] = useState(null)
  const [gpsError, setGpsError] = useState('')
  const [sampling, setSampling] = useState(false)
  const [pointNotice, setPointNotice] = useState(null) // { kind: 'ok'|'warn'|'error', message }
  const samplingRef = useRef(false)
  const samplesRef = useRef([])
  const lastReadingRef = useRef(null) // most recent watchPosition reading: { lat, lon, accuracy, timestamp }
  const nameRef = useRef(null)
  const farmerRef = useRef(null)

  const walking = mode === 'walk' && !walkFinished
  const showEditor = !walking // draw mode, or a finished walk -- same editable FarmMap either way

  // Auto-save draft locally on every change (works with no signal) -- includes walked points so a
  // lost GPS signal or a page reload during a walk doesn't lose captured vertices.
  useEffect(() => {
    const t = new Date().toISOString()
    try {
      localStorage.setItem(DRAFT_KEY, JSON.stringify({
        form, geometry, mode, walkPoints, walkFinished, captureMethod, savedAt: t,
      }))
    } catch { /* ignore */ }
    setSavedAt(t)
  }, [form, geometry, mode, walkPoints, walkFinished, captureMethod])

  // Keep `geometry` in sync with the walked points while actively walking
  useEffect(() => {
    if (!walking) return
    setGeometry(buildGeometry(walkPoints))
  }, [walking, walkPoints])

  // Continuous GPS watch while actively walking: drives the live dot + feeds point sampling
  useEffect(() => {
    if (!walking) { setLivePosition(null); return }
    if (!navigator.geolocation) { setGpsError('GPS is not available in this browser.'); return }
    setGpsError('')
    const id = navigator.geolocation.watchPosition(
      (pos) => {
        const { latitude, longitude, accuracy } = pos.coords
        const reading = { lat: latitude, lon: longitude, accuracy, timestamp: Date.now() }
        lastReadingRef.current = reading
        setLivePosition({ lat: latitude, lon: longitude, accuracy })
        if (samplingRef.current) samplesRef.current.push(reading)
      },
      (e) => setGpsError(e.message || 'GPS error. Check location permission for this site.'),
      { enableHighAccuracy: true, maximumAge: 1000, timeout: 20000 },
    )
    return () => navigator.geolocation.clearWatch(id)
  }, [walking])

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

  const avgAccuracy = useMemo(() => (
    captureMethod === 'gps_walk' && walkPoints.length ? mean(walkPoints.map((p) => p.accuracy)) : null
  ), [captureMethod, walkPoints])
  const flaggedCount = useMemo(() => walkPoints.filter((p) => p.flagged).length, [walkPoints])

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value })
  const canSubmit = form.name && form.farmer_name && stats?.valid && stats.vertices >= 3

  const resetBoundary = () => {
    setGeometry(null)
    setWalkPoints([])
    setWalkFinished(false)
    setCaptureMethod('drawn')
    setPointNotice(null)
  }

  const switchMode = (next) => {
    if (next === mode) return
    const hasProgress = geometry || walkPoints.length > 0
    if (hasProgress && !window.confirm('Switch boundary method? This clears the current boundary.')) return
    resetBoundary()
    setModeState(next)
  }

  const clearDraft = () => {
    localStorage.removeItem(DRAFT_KEY)
    setForm(EMPTY)
    setModeState('draw')
    resetBoundary()
  }

  const addPoint = async () => {
    if (sampling) return
    setPointNotice(null)
    setSampling(true)
    samplingRef.current = true
    // Seed with the latest live reading (watchPosition won't fire again if the device hasn't
    // moved -- e.g. DevTools Sensors or a phone held still -- so without this the window can
    // collect nothing even though the live dot is showing a good fix).
    const last = lastReadingRef.current
    samplesRef.current = last && Date.now() - last.timestamp < FRESH_READING_MS ? [last] : []

    await new Promise((resolve) => setTimeout(resolve, SAMPLE_MS))
    samplingRef.current = false

    let samples = samplesRef.current
    if (!samples.length) {
      const one = await getPositionOnce()
      if (one) { lastReadingRef.current = one; samples = [one] }
    }
    setSampling(false)

    if (!samples.length) {
      setPointNotice({ kind: 'error', message: 'No GPS reading. Move to open sky and try again.' })
      return
    }
    const lat = mean(samples.map((s) => s.lat))
    const lon = mean(samples.map((s) => s.lon))
    const accuracy = mean(samples.map((s) => s.accuracy))
    // eslint-disable-next-line no-console
    console.log(`[walk-the-boundary] samples=${samples.length} avgAccuracy=${accuracy.toFixed(1)}m`)
    if (accuracy > BLOCK_ACCURACY_M) {
      setPointNotice({ kind: 'error', message: 'GPS too weak. Move to open sky and try again.' })
      return
    }
    const flagged = accuracy > GOOD_ACCURACY_M
    setWalkPoints((pts) => [...pts, { lat, lon, accuracy, flagged }])
    setCaptureMethod('gps_walk')
    setGpsError('')
    setPointNotice(flagged
      ? { kind: 'warn', message: `Point added, but accuracy is ±${accuracy.toFixed(1)} m (target: under ${GOOD_ACCURACY_M} m).` }
      : { kind: 'ok', message: `Point added (±${accuracy.toFixed(1)} m).` })
  }

  const undoPoint = () => {
    setPointNotice(null)
    setWalkPoints((pts) => pts.slice(0, -1))
  }

  const finishWalk = () => {
    if (walkPoints.length < 3) return
    setWalkFinished(true)
    setPointNotice(null)
  }

  // Mobile-only sticky save bar: stays clickable even when "not ready" (styled greyed-out, not
  // actually disabled) so a tap can scroll to whichever required field is still empty.
  const stickySave = () => {
    if (!stats?.valid || stats.vertices < 3 || busy) return
    if (!form.name.trim()) { nameRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }); nameRef.current?.focus(); return }
    if (!form.farmer_name.trim()) { farmerRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }); farmerRef.current?.focus(); return }
    submit(true)
  }

  const submit = async (runCheck) => {
    setError(''); setNotice('')
    const body = {
      ...form,
      harvest_date: form.harvest_date || null,
      geometry,
      captured_by: getUser()?.name || '',
      capture_method: captureMethod,
      gps_accuracy_m: avgAccuracy !== null ? Math.round(avgAccuracy * 10) / 10 : null,
      flagged_points: flaggedCount,
    }
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
        <section className="card capture-map-card">
          <div className="card-head"><b>Boundary editor</b>
            <span className={`pill ${stats?.valid ? 'ok' : ''}`}>
              {stats ? (stats.valid ? '● Polygon closed' : '● Fix crossing lines') : 'Draw or walk the boundary ⬠'}
            </span>
          </div>
          <div className="toggle-group">
            <button type="button" className={`toggle-btn ${mode === 'draw' ? 'active' : ''}`}
              disabled={walking && walkPoints.length > 0} onClick={() => switchMode('draw')}>
              ✎ <span className="label-full">Draw on map</span><span className="label-short">Draw</span>
            </button>
            <button type="button" className={`toggle-btn ${mode === 'walk' ? 'active' : ''}`}
              onClick={() => switchMode('walk')}>
              ◎ <span className="label-full">Walk the boundary</span><span className="label-short">Walk</span>
            </button>
          </div>

          {showEditor ? (
            <>
              <FarmMap key="editor" editable geometry={geometry} onChange={setGeometry} />
              <div className="card-foot muted small">
                {captureMethod === 'gps_walk'
                  ? 'Walked boundary — drag any vertex to fine-tune it, or use the polygon tool to redraw it.'
                  : 'Use the polygon tool (⬠) on the left. Follow the outer cultivated edge, exclude roads and ' +
                    'neighbouring plots, and confirm the boundary with the farmer.'}
              </div>
            </>
          ) : (
            <>
              <div className="walk-map">
                <FarmMap key="walk" mode="walk" walkPoints={walkPoints} livePosition={livePosition} />
              </div>
              <div className="stack walk-panel" style={{ gap: 8, padding: 16 }}>
                {gpsError && <p className="small bad-text">⚠ {gpsError}</p>}
                <div className="row between walk-meta">
                  <span className="small muted">
                    Current accuracy: <b>{livePosition ? `${livePosition.accuracy.toFixed(1)} m` : '–'}</b>
                  </span>
                  <span className="small muted">{walkPoints.length} point(s) · {stats ? `${stats.ha.toFixed(2)} ha` : '0.00 ha'}</span>
                </div>
                {pointNotice && (
                  <p className={`small ${pointNotice.kind === 'error' ? 'bad-text' : pointNotice.kind === 'ok' ? 'ok-text' : 'notice'}`}>
                    {pointNotice.kind === 'error' ? '✗' : pointNotice.kind === 'warn' ? '⚠' : '✓'} {pointNotice.message}
                  </p>
                )}
                <div className="row gap walk-actions">
                  <button type="button" className="btn primary" disabled={sampling} onClick={addPoint}>
                    {sampling ? 'Reading GPS…' : '◎ Add point here'}
                  </button>
                  <button type="button" className="btn" disabled={!walkPoints.length} onClick={undoPoint}>↺ Undo last point</button>
                  <button type="button" className="btn" disabled={walkPoints.length < 3} onClick={finishWalk}>Finish boundary ✓</button>
                </div>
              </div>
            </>
          )}
        </section>

        <aside className="stack capture-aside">
          <section className="card pad">
            <b>Farm details</b>
            <label className="field-label">Farm name *</label>
            <input ref={nameRef} value={form.name} onChange={set('name')} placeholder="Adom Cocoa Farm" />
            <label className="field-label">Farmer name *</label>
            <input ref={farmerRef} value={form.farmer_name} onChange={set('farmer_name')} placeholder="Ama Mensah" />
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
            {captureMethod === 'gps_walk' && (
              <p className="small muted">
                GPS walk · avg ±{avgAccuracy?.toFixed(1)} m{flaggedCount ? ` · ${flaggedCount} flagged point(s)` : ''}
              </p>
            )}
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

      {showEditor && (
        <div className="draw-save-bar">
          <button type="button" className={`btn primary wide ${canSubmit ? 'ready' : ''}`}
            onClick={stickySave}>
            {busy || 'Save & run check →'}
          </button>
        </div>
      )}
    </div>
  )
}
