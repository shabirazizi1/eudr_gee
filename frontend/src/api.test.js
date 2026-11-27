// Outbox retry rules: `npm test` (node:test, no extra dependencies).
import { test } from 'node:test'
import assert from 'node:assert/strict'

const store = new Map()
globalThis.localStorage = {
  getItem: (k) => store.get(k) ?? null,
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
}
Object.defineProperty(globalThis, 'navigator', { value: { onLine: true }, configurable: true })

const { queueFarm, flushOutbox, outboxCount, failedOutbox, discardFailed } = await import('./api.js')

const respond = (status) => async () => {
  if (status === 'network') throw new TypeError('Failed to fetch')
  return { ok: status < 400, status, statusText: 'x', json: async () => ({ detail: `server said ${status}` }) }
}

test('4xx parks the draft as failed, network errors and 5xx keep retrying', async () => {
  store.clear()
  queueFarm({ name: 'Forbidden farm' })
  globalThis.fetch = respond(403)
  assert.equal(await flushOutbox(), 0)
  assert.equal(outboxCount(), 0)
  const [failed] = failedOutbox()
  assert.equal(failed.name, 'Forbidden farm')
  assert.equal(failed.error, 'not permitted for your role')

  // queuedAt is a ms timestamp and discardFailed matches on it; a fast CI box can queue both in the same ms.
  await new Promise((r) => setTimeout(r, 2))
  queueFarm({ name: 'Bad geometry' })
  globalThis.fetch = respond(422)
  await flushOutbox()
  assert.equal(failedOutbox()[1].error, 'server said 422')

  for (const status of ['network', 500]) {
    queueFarm({ name: 'Retry me' })
    globalThis.fetch = respond(status)
    await flushOutbox()
    assert.equal(outboxCount(), 1, `${status} should stay queued`)
    globalThis.fetch = respond(201)
    assert.equal(await flushOutbox(), 1)
    assert.equal(outboxCount(), 0)
  }
  assert.equal(failedOutbox().length, 2)

  discardFailed(failed.queuedAt)
  assert.deepEqual(failedOutbox().map((f) => f.name), ['Bad geometry'])
})
