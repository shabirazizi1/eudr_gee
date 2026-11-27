// Thin API client + offline outbox (F2: drafts saved locally, synced when online)

const OUTBOX_KEY = 'eudr_outbox'
const FAILED_KEY = 'eudr_outbox_failed' // 4xx'd drafts, kept until the user discards them

async function request(path, options = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    let msg = res.statusText
    try {
      const body = await res.json()
      msg = Array.isArray(body.detail) ? body.detail.join(' ') : body.detail || msg
    } catch { /* ignore */ }
    const err = new Error(msg)
    err.status = res.status
    throw err
  }
  return res.json()
}

export const api = {
  health: () => request('/health'),
  summary: () => request('/summary'),
  farms: (season) => request(season ? `/farms?season=${encodeURIComponent(season)}` : '/farms'),
  farm: (id) => request(`/farms/${id}`),
  createFarm: (body) => request('/farms', { method: 'POST', body: JSON.stringify(body) }),
  verify: (id, role) => request(`/farms/${id}/verify?role=${encodeURIComponent(role || '')}`, { method: 'POST' }),
  remapFarm: (id, body) => request(`/farms/${id}/remap`, { method: 'POST', body: JSON.stringify(body) }),
  boundaryVersions: (id) => request(`/farms/${id}/boundary_versions`),
  reviewFarm: (id, body) => request(`/farms/${id}/review`, { method: 'POST', body: JSON.stringify(body) }),
  reportUrl: (id) => `/api/farms/${id}/report.pdf`,
  batches: () => request('/batches'),
  batch: (id) => request(`/batches/${id}`),
  createBatch: (body) => request('/batches', { method: 'POST', body: JSON.stringify(body) }),
  approveBatch: (id, body) => request(`/batches/${id}/approve`, { method: 'POST', body: JSON.stringify(body) }),
  rejectBatch: (id, body) => request(`/batches/${id}/reject`, { method: 'POST', body: JSON.stringify(body) }),
  cancelBatch: (id, body) => request(`/batches/${id}/cancel`, { method: 'POST', body: JSON.stringify(body) }),
  batchReportUrl: (id) => `/api/batches/${id}/report.pdf`,
  ddsUrl: (id) => `/api/batches/${id}/dds`,
  geolocationUrl: (id) => `/api/batches/${id}/geolocation.geojson`,
  bagLabelsUrl: (id) => `/api/batches/${id}/bag_labels.pdf`,
  bag: (bagId) => request(`/bags/${bagId}`),
  // ---- bulk verify (F2.b) ----
  startBulkVerify: (farmIds, triggeredBy = '', byRole = '') => request('/bulk/verify', {
    method: 'POST',
    body: JSON.stringify({ farm_ids: farmIds, triggered_by: triggeredBy, by_role: byRole }),
  }),
  bulkJob: (jobId) => request(`/bulk/${jobId}`),
  bulkSummary: (jobId) => request(`/bulk/${jobId}/summary`),
}

// ---- offline outbox -------------------------------------------------------

function readList(key) {
  try { return JSON.parse(localStorage.getItem(key)) || [] } catch { return [] }
}
function writeList(key, items) {
  try { localStorage.setItem(key, JSON.stringify(items)) } catch { /* ignore */ }
}
const readOutbox = () => readList(OUTBOX_KEY)
const writeOutbox = (items) => writeList(OUTBOX_KEY, items)

export function failedOutbox() {
  return readList(FAILED_KEY)
}

export function discardFailed(queuedAt) {
  writeList(FAILED_KEY, failedOutbox().filter((f) => f.queuedAt !== queuedAt))
}

export function outboxCount() {
  return readOutbox().length
}

export function queueFarm(body) {
  writeOutbox([...readOutbox(), { ...body, queuedAt: new Date().toISOString() }])
}

// Try to send queued farms. Returns the number synced.
export async function flushOutbox() {
  const items = readOutbox()
  if (!items.length || !navigator.onLine) return 0
  const left = []
  const failed = []
  let synced = 0
  for (const item of items) {
    try {
      const { queuedAt, ...body } = item
      await api.createFarm(body)
      synced++
    } catch (e) {
      // 4xx (bad geometry, wrong role...) won't succeed on retry: park it where the user sees it.
      // Network errors (no status) and 5xx stay queued and are retried.
      if (e.status >= 400 && e.status < 500) {
        failed.push({ ...item, status: e.status, error: e.status === 403 ? 'not permitted for your role' : e.message })
      } else {
        left.push(item)
      }
    }
  }
  writeOutbox(left)
  if (failed.length) writeList(FAILED_KEY, [...failedOutbox(), ...failed])
  return synced
}