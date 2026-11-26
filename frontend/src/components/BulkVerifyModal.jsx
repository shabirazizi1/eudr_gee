import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api.js'
import RiskBadge from './RiskBadge.jsx'

const POLL_MS = 2000

/**
 * Bulk satellite screening: pick farms -> watch one job's progress -> summary.
 * Polls /api/bulk/{id} every 2 s; the job itself runs server-side under a thread
 * pool, so closing this modal never cancels it -- results are idempotent.
 */
export default function BulkVerifyModal({ farms, onClose, onDone }) {
  const [selected, setSelected] = useState(() => farms.map((f) => f.id))
  const [job, setJob] = useState(null)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState('')
  const timer = useRef(null)

  const toggle = (id) =>
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]))

  const start = async () => {
    setError('')
    setStarting(true)
    try {
      setJob(await api.startBulkVerify(selected))
    } catch (e) {
      setError(e.message)
    } finally {
      setStarting(false)
    }
  }

  // Poll until the job completes. Cleared on unmount so a closed modal stops hitting the API.
  useEffect(() => {
    if (!job || job.status === 'done' || job.status === 'failed') return undefined
    timer.current = setInterval(async () => {
      try {
        const next = await api.bulkJob(job.job_id)
        setJob(next)
        if (next.status === 'done' || next.status === 'failed') {
          clearInterval(timer.current)
          onDone?.(next)
        }
      } catch (e) {
        setError(e.message)
        clearInterval(timer.current)
      }
    }, POLL_MS)
    return () => clearInterval(timer.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.job_id, job?.status])

  const running = job && job.status !== 'done' && job.status !== 'failed'
  const results = job?.results || []
  const counts = { Low: 0, Medium: 0, High: 0 }
  results.forEach((r) => r.status === 'ok' && counts[r.level]++)

  return (
    <div className="modal-backdrop" onClick={running ? undefined : onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="card-head">
          <b>{job ? `Screening job ${job.job_id}` : 'Run satellite screening'}</b>
          <button className="link-btn" onClick={onClose} disabled={running}>
            {running ? 'Running…' : '✕ Close'}
          </button>
        </div>

        {!job ? (
          <>
            <p className="small muted">
              {selected.length} of {farms.length} farms selected. Each check takes 10–40 s;
              three run at a time, so a large batch takes a few minutes. You can close this
              window — the job keeps running.
            </p>
            <div className="bulk-list">
              {farms.map((f) => (
                <label key={f.id} className="bulk-row">
                  <input type="checkbox" checked={selected.includes(f.id)} onChange={() => toggle(f.id)} />
                  <span className="bulk-name">
                    <b>{f.name}</b>
                    <small className="muted">{f.code} · {f.area_ha} ha</small>
                  </span>
                  <RiskBadge level={f.risk_level} />
                </label>
              ))}
              {!farms.length && <p className="small muted">Every farm has already been screened.</p>}
            </div>
            {error && <p className="small bad-text">{error}</p>}
            <div className="row between" style={{ marginTop: 12 }}>
              <span className="small muted">{selected.length} farm(s) queued</span>
              <button className="btn primary" disabled={!selected.length || starting} onClick={start}>
                {starting ? 'Starting…' : 'Run screening →'}
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="bulk-progress">
              <div className="bulk-bar">
                <i style={{ width: `${job.progress_pct}%` }} />
              </div>
              <div className="row between small">
                <span>
                  <b>{job.done}</b> / {job.total} farms · {job.progress_pct}%
                  {job.failed > 0 && <> · <span className="bad-text">{job.failed} failed</span></>}
                </span>
                <span className="muted">
                  {running ? (job.eta_seconds ? `≈ ${job.eta_seconds}s left` : 'Starting…') : 'Complete'}
                </span>
              </div>
            </div>

            {results.length > 0 && (
              <div className="bulk-list">
                {[...results].reverse().map((r, i) => (
                  <div key={`${r.code}-${i}`} className="bulk-row">
                    {r.status === 'ok' ? (
                      <>
                        <RiskBadge level={r.level} />
                        <span className="bulk-name">
                          <b>{r.code} · {r.name}</b>
                          <small className="muted">
                            {r.score}/100 · {r.seconds}s
                            {r.flags?.length ? ` · ⚠ ${r.flags.length} flag(s)` : ''}
                          </small>
                        </span>
                        <Link className="link-btn" to={`/farms/${r.farm_id}`}>Open →</Link>
                      </>
                    ) : (
                      <>
                        <span className="pill warn">Failed</span>
                        <span className="bulk-name">
                          <b>{r.code}</b>
                          <small className="bad-text">{r.message}</small>
                        </span>
                      </>
                    )}
                  </div>
                ))}
              </div>
            )}

            {job.status === 'done' && (
              <div className="bulk-summary">
                <b>Result</b>
                <div className="row gap small">
                  <span><b>{counts.Low}</b> Low</span>
                  <span><b>{counts.Medium}</b> Medium</span>
                  <span><b>{counts.High}</b> High</span>
                  {job.failed > 0 && <span className="bad-text"><b>{job.failed}</b> failed</span>}
                </div>
                <p className="small muted">
                  {job.failed > 0
                    ? 'Failed farms keep their previous result — re-run them individually from the farm page.'
                    : 'Every farm was screened with the current cutoff and datasets.'}
                </p>
              </div>
            )}
            {error && <p className="small bad-text">{error}</p>}
            <div className="row between" style={{ marginTop: 12 }}>
              <span className="small muted">
                {running ? 'Polling every 2 s…' : `Finished ${new Date(job.finished_at).toLocaleTimeString()}`}
              </span>
              <button className="btn primary" onClick={onClose} disabled={running}>
                {running ? 'Working…' : 'Done'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
