// Thin API client + offline outbox (F2: drafts saved locally, synced when online)

const OUTBOX_KEY = 'eudr_outbox'

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
  farms: () => request('/farms'),
  farm: (id) => request(`/farms/${id}`),
  createFarm: (body) => request('/farms', { method: 'POST', body: JSON.stringify(body) }),
  verify: (id) => request(`/farms/${id}/verify`, { method: 'POST' }),
  reportUrl: (id) => `/api/farms/${id}/report.pdf`,
}

// ---- offline outbox -------------------------------------------------------

function readOutbox() {
  try { return JSON.parse(localStorage.getItem(OUTBOX_KEY)) || [] } catch { return [] }
}
function writeOutbox(items) {
  try { localStorage.setItem(OUTBOX_KEY, JSON.stringify(items)) } catch { /* ignore */ }
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
  let synced = 0
  for (const item of items) {
    try {
      const { queuedAt, ...body } = item
      await api.createFarm(body)
      synced++
    } catch (e) {
      // 422 = invalid geometry: drop it; anything else: keep for later
      if (e.status !== 422) left.push(item)
    }
  }
  writeOutbox(left)
  return synced
}
