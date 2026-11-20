const base = '/api'

async function req(path, opts = {}) {
  const res = await fetch(base + path, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  })
  const text = await res.text()
  let data
  try { data = text ? JSON.parse(text) : null } catch { data = text }
  if (!res.ok) {
    const msg = (data && data.detail) || text || res.statusText
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg))
  }
  return data
}

const q = (obj) => {
  const sp = new URLSearchParams(
    Object.fromEntries(Object.entries(obj || {}).filter(([, v]) => v !== '' && v != null)),
  )
  const s = sp.toString()
  return s ? `?${s}` : ''
}

export const api = {
  overview: () => req('/overview'),
  meters: () => req('/meters'),
  estimates: (periodId) => req('/estimates' + q({ period_id: periodId })),
  estimateDetail: (id) => req(`/estimates/${id}`),
  estimateCompare: (id, fromNo, toNo) =>
    req(`/estimates/${id}/compare` + q({ from_no: fromNo, to_no: toNo })),
  postEstimate: (body) => req('/estimates', { method: 'POST', body: JSON.stringify(body) }),
  reviseEstimate: (id, body) => req(`/estimates/${id}/revisions`, { method: 'POST', body: JSON.stringify(body) }),
  actuals: (periodId) => req('/actuals' + q({ period_id: periodId })),
  postActual: (body) => req('/actuals', { method: 'POST', body: JSON.stringify(body) }),
  periods: () => req('/periods'),
  align: (period_id) => req('/align', { method: 'POST', body: JSON.stringify({ period_id }) }),
  batches: (periodId) => req('/batches' + q({ period_id: periodId })),
  batchSnapshot: (id) => req(`/batches/${id}/snapshot`),
  variances: (params) => req('/variances' + q(params)),
  setFate: (id, fate) => req(`/variances/${id}/fate`, { method: 'POST', body: JSON.stringify({ fate }) }),
  seal: (body) => req('/seal', { method: 'POST', body: JSON.stringify(body) }),
  settings: () => req('/settings'),
  putSetting: (key, value) => req(`/settings/${key}`, { method: 'PUT', body: JSON.stringify({ value }) }),
}
