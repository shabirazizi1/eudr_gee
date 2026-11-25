import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import area from '@turf/area'
import length from '@turf/length'
import kinks from '@turf/kinks'
import { api, queueFarm } from '../api.js'
import { getUser } from '../App.jsx'
import FarmMap from '../components/FarmMap.jsx'

// F2 – Farm registry & GPS boundary capture (HLR-01: "Walk the boundary" GPS mode)
const DRAFT_KEY = 'eudr_capture_draft'
const EMPTY = { name: '', farmer_name: '', village: '', harvest_date: '' }
const today = () => new Date().toISOString().slice(0, 10)

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result)
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}
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
// Resolves { errorCode } instead of null on failure so callers can tell "permission denied"
// (code 1) apart from a real timeout/no-signal (codes 2/3).
function getPositionOnce() {
  return new Promise((resolve) => {
    if (!navigator.geolocation) { resolve({ errorCode: 2 }); return }
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({
        lat: pos.coords.latitude, lon: pos.coords.longitude, accuracy: pos.coords.accuracy, timestamp: Date.now(),
      }),
      (err) => resolve({ errorCode: err.code }),
      { enableHighAccuracy: true, maximumAge: 0, timeout: 10000 },
    )
  })
}

const BLOCKED_LOCATION_MESSAGE = "Location access is blocked. Allow location for this site in your browser's site settings, then reload."
const NO_SIGNAL_MESSAGE = 'No GPS reading. Move to open sky and try again.'
const WEAK_SIGNAL_MESSAGE = 'GPS too weak. Move to open sky and try again.'

function buildGeometry(points) {
  if (points.length < 3) return null
  const ring = points.map((p) => [p.lon, p.lat])
  ring.push(ring[0])
  return { type: 'Polygon', coordinates: [ring] }
}

const MAX_POINT_CAPTURE_HA = 4 // EUDR: plots this size or smaller may be declared as a single point

export default function Capture() {
  const nav = useNavigate()
  const { id: remapFarmId } = useParams() // set only on /farms/:id/remap (issue #4)
  const isRemap = Boolean(remapFarmId)
  const draft = isRemap ? null : loadDraft() // remap never touches the new-farm draft
  const [form, setForm] = useState(draft?.form || EMPTY)
  const [geometry, setGeometry] = useState(draft?.geometry || null)
  const [savedAt, setSavedAt] = useState(draft?.savedAt || null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [remapFarm, setRemapFarm] = useState(null)

  useEffect(() => {
    if (!isRemap) return
    api.farm(remapFarmId).then((f) => {
      setRemapFarm(f)
      if (f.capture_method === 'gps_point') {
        setModeState('point')
        setDeclaredAreaHa(String(f.declared_area_ha ?? ''))
      } else {
        setGeometry(f.geometry)
        setCaptureMethod(f.capture_method)
      }
    }).catch((e) => setError(e.message))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isRemap, remapFarmId])

  // Farmer consent (issue #7): attestation (checkbox + agent + date) is required; a signed-form
  // photo is optional on top. Farms cannot be saved -- online, offline, or via the sticky bar --
  // without it.
  const [consentGiven, setConsentGiven] = useState(draft?.consentGiven || false)
  const [consentDate, setConsentDate] = useState(draft?.consentDate || today())
  const [consentPhoto, setConsentPhoto] = useState(draft?.consentPhoto || null)
  const consentRef = useRef(null)

  // Walk-the-boundary / single-point state
  const [mode, setModeState] = useState(draft?.mode || 'draw')             // 'draw' | 'walk' | 'point'
  const [walkPoints, setWalkPoints] = useState(draft?.walkPoints || [])    // [{lat, lon, accuracy, flagged}]
  const [walkFinished, setWalkFinished] = useState(draft?.walkFinished || false)
  const [captureMethod, setCaptureMethod] = useState(draft?.captureMethod || 'drawn')
  const [pointCoord, setPointCoord] = useState(draft?.pointCoord || null) // {lat, lon, accuracy, flagged}
  const [declaredAreaHa, setDeclaredAreaHa] = useState(draft?.declaredAreaHa || '') // issue #5
  const [livePosition, setLivePosition] = useState(null)
  const [gpsError, setGpsError] = useState('')
  const [sampling, setSampling] = useState(false)
  const [pointNotice, setPointNotice] = useState(null) // { kind: 'ok'|'warn'|'error', message }
  const samplingRef = useRef(false)
  const samplesRef = useRef([])
  const lastReadingRef = useRef(null) // most recent watchPosition reading: { lat, lon, accuracy, timestamp }
  const lastGpsErrorCodeRef = useRef(null) // most recent GeolocationPositionError.code (1 = denied)
  const nameRef = useRef(null)
  const farmerRef = useRef(null)

  const walking = mode === 'walk' && !walkFinished
  const pointMode = mode === 'point'
  const showEditor = mode === 'draw' || (mode === 'walk' && walkFinished)
  const declaredAreaNum = parseFloat(declaredAreaHa)
  const gpsLiveActive = walking || pointMode // both need the live GPS dot + sampling

  // Auto-save draft locally on every change (works with no signal) -- includes walked points so a
  // lost GPS signal or a page reload during a walk doesn't lose captured vertices.
  useEffect(() => {
    if (isRemap) return // remapping an existing farm never persists to the new-farm draft
    const t = new Date().toISOString()
    try {
      localStorage.setItem(DRAFT_KEY, JSON.stringify({
        form, geometry, mode, walkPoints, walkFinished, captureMethod, pointCoord, declaredAreaHa,
        consentGiven, consentDate, consentPhoto, savedAt: t,
      }))
    } catch { /* ignore */ }
    setSavedAt(t)
  }, [isRemap, form, geometry, mode, walkPoints, walkFinished, captureMethod, pointCoord, declaredAreaHa,
      consentGiven, consentDate, consentPhoto])

  // Keep `geometry` in sync with the walked points while actively walking
  useEffect(() => {
    if (!walking) return
    setGeometry(buildGeometry(walkPoints))
  }, [walking, walkPoints])

  // Keep `geometry` in sync with the captured point (issue #5)
  useEffect(() => {
    if (!pointMode) return
    setGeometry(pointCoord ? { type: 'Point', coordinates: [pointCoord.lon, pointCoord.lat] } : null)
  }, [pointMode, pointCoord])

  // Continuous GPS watch while actively walking or capturing a point: drives the live dot + feeds sampling
  useEffect(() => {
    if (!gpsLiveActive) { setLivePosition(null); return }
    if (!navigator.geolocation) { setGpsError('GPS is not available in this browser.'); return }
    setGpsError('')
    lastGpsErrorCodeRef.current = null
    const id = navigator.geolocation.watchPosition(
      (pos) => {
        const { latitude, longitude, accuracy } = pos.coords
        const reading = { lat: latitude, lon: longitude, accuracy, timestamp: Date.now() }
        lastReadingRef.current = reading
        setLivePosition({ lat: latitude, lon: longitude, accuracy })
        if (samplingRef.current) samplesRef.current.push(reading)
      },
      (e) => {
        lastGpsErrorCodeRef.current = e.code
        setGpsError(e.code === 1 ? BLOCKED_LOCATION_MESSAGE : (e.message || 'GPS error. Check location permission for this site.'))
      },
      { enableHighAccuracy: true, maximumAge: 1000, timeout: 20000 },
    )
    return () => navigator.geolocation.clearWatch(id)
  }, [gpsLiveActive])

  const stats = useMemo(() => {
    if (!geometry || geometry.type === 'Point') return null
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

  const avgAccuracy = useMemo(() => {
    if (captureMethod === 'gps_walk' && walkPoints.length) return mean(walkPoints.map((p) => p.accuracy))
    if (captureMethod === 'gps_point' && pointCoord) return pointCoord.accuracy
    return null
  }, [captureMethod, walkPoints, pointCoord])
  const flaggedCount = useMemo(() => (
    captureMethod === 'gps_point' ? (pointCoord?.flagged ? 1 : 0) : walkPoints.filter((p) => p.flagged).length
  ), [captureMethod, walkPoints, pointCoord])

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value })
  const boundaryValid = pointMode
    ? Boolean(pointCoord) && declaredAreaNum > 0 && declaredAreaNum <= MAX_POINT_CAPTURE_HA
    : Boolean(stats?.valid && stats.vertices >= 3)
  const canSubmit = isRemap
    ? boundaryValid
    : form.name && form.farmer_name && boundaryValid && consentGiven && consentDate

  const onConsentPhoto = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setConsentPhoto(await fileToBase64(file))
  }

  const resetBoundary = () => {
    setGeometry(null)
    setWalkPoints([])
    setWalkFinished(false)
    setCaptureMethod('drawn')
    setPointNotice(null)
    setPointCoord(null)
    setDeclaredAreaHa('')
  }

  const switchMode = (next) => {
    if (next === mode) return
    const hasProgress = geometry || walkPoints.length > 0 || pointCoord
    if (hasProgress && !window.confirm('Switch boundary method? This clears the current boundary.')) return
    resetBoundary()
    setModeState(next)
  }

  const clearDraft = () => {
    if (isRemap) { resetBoundary(); return } // never touches the shared new-farm draft
    localStorage.removeItem(DRAFT_KEY)
    setForm(EMPTY)
    setModeState('draw')
    resetBoundary()
    setConsentGiven(false)
    setConsentDate(today())
    setConsentPhoto(null)
  }

  // Shared by "Add point here" (walk) and "Capture point" (single point, issue #5): a 3 s
  // averaged GPS reading, same accuracy rules for both.
  const sampleGps = async () => {
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
    let deniedCode = lastGpsErrorCodeRef.current === 1
    if (!samples.length) {
      const one = await getPositionOnce()
      if (one.errorCode) deniedCode = deniedCode || one.errorCode === 1
      else { lastReadingRef.current = one; samples = [one] }
    }
    setSampling(false)

    if (!samples.length) return { error: deniedCode ? BLOCKED_LOCATION_MESSAGE : NO_SIGNAL_MESSAGE }
    const lat = mean(samples.map((s) => s.lat))
    const lon = mean(samples.map((s) => s.lon))
    const accuracy = mean(samples.map((s) => s.accuracy))
    // eslint-disable-next-line no-console
    console.log(`[gps-sample] samples=${samples.length} avgAccuracy=${accuracy.toFixed(1)}m`)
    if (accuracy > BLOCK_ACCURACY_M) return { error: WEAK_SIGNAL_MESSAGE }
    return { lat, lon, accuracy, flagged: accuracy > GOOD_ACCURACY_M }
  }

  const addPoint = async () => {
    if (sampling) return
    setPointNotice(null)
    const r = await sampleGps()
    if (r.error) { setPointNotice({ kind: 'error', message: r.error }); return }
    setWalkPoints((pts) => [...pts, { lat: r.lat, lon: r.lon, accuracy: r.accuracy, flagged: r.flagged }])
    setCaptureMethod('gps_walk')
    setGpsError('')
    setPointNotice(r.flagged
      ? { kind: 'warn', message: `Point added, but accuracy is ±${r.accuracy.toFixed(1)} m (target: under ${GOOD_ACCURACY_M} m).` }
      : { kind: 'ok', message: `Point added (±${r.accuracy.toFixed(1)} m).` })
  }

  const capturePoint = async () => {
    if (sampling) return
    setPointNotice(null)
    const r = await sampleGps()
    if (r.error) { setPointNotice({ kind: 'error', message: r.error }); return }
    setPointCoord({ lat: r.lat, lon: r.lon, accuracy: r.accuracy, flagged: r.flagged })
    setCaptureMethod('gps_point')
    setGpsError('')
    setPointNotice(r.flagged
      ? { kind: 'warn', message: `Point captured, but accuracy is ±${r.accuracy.toFixed(1)} m (target: under ${GOOD_ACCURACY_M} m).` }
      : { kind: 'ok', message: `Point captured (±${r.accuracy.toFixed(1)} m).` })
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
    if (!boundaryValid || busy) return
    if (!isRemap) {
      if (!form.name.trim()) { nameRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }); nameRef.current?.focus(); return }
      if (!form.farmer_name.trim()) { farmerRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }); farmerRef.current?.focus(); return }
      if (!consentGiven || !consentDate) { consentRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }); return }
    }
    submit(true)
  }

  const submitRemap = async (runCheck) => {
    const body = {
      geometry,
      captured_by: getUser()?.name || '',
      capture_method: captureMethod,
      gps_accuracy_m: avgAccuracy !== null ? Math.round(avgAccuracy * 10) / 10 : null,
      flagged_points: flaggedCount,
      declared_area_ha: pointMode ? declaredAreaNum : null,
    }
    if (!navigator.onLine) { setError("You're offline — reconnect to save this remap."); return }
    try {
      setBusy('Saving new boundary…')
      await api.remapFarm(remapFarmId, body)
      if (runCheck) {
        setBusy('Running satellite check (can take 10–40 s)…')
        await api.verify(remapFarmId)
      }
      nav(`/farms/${remapFarmId}`)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy('')
    }
  }

  const submit = async (runCheck) => {
    setError(''); setNotice('')
    if (isRemap) { await submitRemap(runCheck); return }
    const body = {
      ...form,
      harvest_date: form.harvest_date || null,
      geometry,
      captured_by: getUser()?.name || '',
      capture_method: captureMethod,
      gps_accuracy_m: avgAccuracy !== null ? Math.round(avgAccuracy * 10) / 10 : null,
      flagged_points: flaggedCount,
      declared_area_ha: pointMode ? declaredAreaNum : null,
      consent_given: consentGiven,
      consent_date: consentDate,
      consent_photo_base64: consentPhoto || null,
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
      <div className="crumbs">Farm registry / {isRemap ? remapFarm?.name || 'Remap' : 'New farm'} / Boundary capture</div>
      <div className="page-head">
        <div>
          <h1>{isRemap ? 'Remap the boundary.' : 'Map the farm. Ground the evidence.'}</h1>
          <p className="muted">
            {isRemap
              ? `New boundary for ${remapFarm?.name || 'this farm'} (${remapFarm?.code || ''}). ` +
                'Saving creates a new version -- the old one is kept, and the farm is re-screened as Not screened.'
              : 'Capture a precise boundary before satellite screening.'}
          </p>
        </div>
      </div>

      <div className="grid-capture">
        <section className="card capture-map-card">
          <div className="card-head"><b>Boundary editor</b>
            <span className={`pill ${boundaryValid ? 'ok' : ''}`}>
              {pointMode
                ? (pointCoord ? '● Point captured' : 'Capture a single point ◎')
                : stats ? (stats.valid ? '● Polygon closed' : '● Fix crossing lines') : 'Draw or walk the boundary ⬠'}
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
            <button type="button" className={`toggle-btn ${mode === 'point' ? 'active' : ''}`}
              onClick={() => switchMode('point')}>
              ⚲ <span className="label-full">Single point (≤ 4 ha)</span><span className="label-short">Point</span>
            </button>
          </div>

          {pointMode ? (
            <>
              <div className="walk-map">
                <FarmMap key="point" mode="walk" walkPoints={pointCoord ? [pointCoord] : []} livePosition={livePosition} />
              </div>
              <div className="stack walk-panel" style={{ gap: 8, padding: 16 }}>
                {gpsError && <p className="small bad-text">⚠ {gpsError}</p>}
                <div className="row between walk-meta">
                  <span className="small muted">
                    Current accuracy: <b>{livePosition ? `${livePosition.accuracy.toFixed(1)} m` : '–'}</b>
                  </span>
                </div>
                {pointNotice && (
                  <p className={`small ${pointNotice.kind === 'error' ? 'bad-text' : pointNotice.kind === 'ok' ? 'ok-text' : 'notice'}`}>
                    {pointNotice.kind === 'error' ? '✗' : pointNotice.kind === 'warn' ? '⚠' : '✓'} {pointNotice.message}
                  </p>
                )}
                <div className="row gap walk-actions">
                  <button type="button" className="btn primary" disabled={sampling} onClick={capturePoint}>
                    {sampling ? 'Reading GPS…' : pointCoord ? '◎ Re-capture point' : '◎ Capture point here'}
                  </button>
                </div>
              </div>
            </>
          ) : showEditor ? (
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
          {isRemap ? (
            <section className="card pad">
              <b>Remapping an existing farm</b>
              <p className="small muted" style={{ marginTop: 6 }}>
                Name, farmer and consent stay as they are on {remapFarm?.name || 'this farm'} --
                remapping only replaces the boundary (draw, walk, or a single point for plots
                4 ha or smaller). A new, read-only version is kept alongside every earlier one.
              </p>
            </section>
          ) : (
            <>
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

              <section className="card pad" ref={consentRef}>
                <b>Farmer consent *</b>
                <label className="row gap" style={{ marginTop: 8, alignItems: 'flex-start' }}>
                  <input type="checkbox" checked={consentGiven} onChange={(e) => setConsentGiven(e.target.checked)} />
                  <span className="small">
                    The farmer has given consent to collect and share their personal and location
                    data for this registration. Recorded by {getUser()?.name || 'this agent'}.
                  </span>
                </label>
                <label className="field-label">Consent date *</label>
                <input type="date" value={consentDate} onChange={(e) => setConsentDate(e.target.value)} />
                <label className="field-label">Signed consent form photo (optional)</label>
                <input type="file" accept="image/*" capture="environment" onChange={onConsentPhoto} />
                {consentPhoto && <p className="small ok-text">✓ Photo attached</p>}
              </section>
            </>
          )}

          <section className="card pad">
            <div className="card-head tight"><b>Captured boundary</b><small className="muted">WGS 84</small></div>
            {pointMode ? (
              <>
                <label className="field-label">Declared area (ha) *</label>
                <input type="number" min="0.01" max={MAX_POINT_CAPTURE_HA} step="0.01" value={declaredAreaHa}
                  onChange={(e) => setDeclaredAreaHa(e.target.value)} placeholder="e.g. 1.5" />
                <p className="small muted">Plots over {MAX_POINT_CAPTURE_HA} ha need a full boundary (Draw or Walk).</p>
                {declaredAreaHa && declaredAreaNum > MAX_POINT_CAPTURE_HA && (
                  <p className="small bad-text">⚠ Plots over {MAX_POINT_CAPTURE_HA} ha need a full boundary.</p>
                )}
                {pointCoord && (
                  <p className="small muted" style={{ marginTop: 8 }}>
                    Point · ±{pointCoord.accuracy.toFixed(1)} m{pointCoord.flagged ? ' (flagged)' : ''} ·{' '}
                    {pointCoord.lat.toFixed(5)}, {pointCoord.lon.toFixed(5)}
                  </p>
                )}
              </>
            ) : (
              <>
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
              </>
            )}
            {error && <p className="small bad-text">{error}</p>}
            {notice && <p className="small notice">{notice}</p>}
            <button className="btn primary wide" disabled={!canSubmit || !!busy} onClick={() => submit(true)}>
              {busy || (isRemap ? 'Save new boundary & run check →' : 'Save & run satellite check →')}
            </button>
            <button className="btn wide" disabled={!canSubmit || !!busy} onClick={() => submit(false)}>Save only</button>
            <button className="link-btn small" onClick={clearDraft}>{isRemap ? 'Reset boundary' : 'Clear draft'}</button>
          </section>
          {!isRemap && (
            <p className="muted small">Draft saved locally {savedAt ? new Date(savedAt).toLocaleTimeString() : ''}</p>
          )}
        </aside>
      </div>

      {(showEditor || pointMode) && (
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
