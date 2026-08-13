/**
 * Thin API client.
 *
 * Every network failure is surfaced as an ApiError with a usable message so
 * panels can show what actually went wrong instead of an empty state that
 * looks identical to "no data".
 */

export class ApiError extends Error {
  constructor(message, status, field, body) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.field = field;
    this.body = body;
  }
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      credentials: 'same-origin',
      ...options,
      headers: {
        Accept: 'application/json',
        ...(options.body && !(options.body instanceof FormData)
          ? { 'Content-Type': 'application/json' }
          : {}),
        ...(options.headers || {}),
      },
    });
  } catch (err) {
    throw new ApiError('Network unreachable — is the server running?', 0, null, null);
  }

  const isJson = (response.headers.get('content-type') || '').includes('application/json');
  const body = isJson ? await response.json().catch(() => null) : null;

  if (!response.ok) {
    const message = (body && (body.error || body.message)) || `Request failed (${response.status})`;
    throw new ApiError(message, response.status, body && body.field, body);
  }
  return body;
}

const qs = (params) => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params || {})) {
    if (value === null || value === undefined || value === '') continue;
    search.set(key, value);
  }
  const str = search.toString();
  return str ? `?${str}` : '';
};

/** Translate store filters into the query parameters the API expects. */
export function filterParams(filters, cityId) {
  return {
    city: cityId,
    window: filters.window || '',
    min_confidence: filters.minConfidence || '',
    source: filters.source || '',
    status: filters.status || '',
    q: filters.q || '',
  };
}

export const api = {
  // --- sightings
  sightings: (params) => request(`/api/sightings${qs(params)}`),
  sighting: (id) => request(`/api/sightings/${id}`),
  createSighting: (payload) =>
    request('/api/sightings', {
      method: 'POST',
      body: payload instanceof FormData ? payload : JSON.stringify(payload),
    }),
  updateSighting: (id, payload) =>
    request(`/api/sightings/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteSighting: (id) => request(`/api/sightings/${id}`, { method: 'DELETE' }),
  reanalyze: (id) => request(`/api/sightings/${id}/reanalyze`, { method: 'POST' }),

  // --- prediction
  prediction: (city, refresh) => request(`/api/predictions${qs({ city, refresh: refresh ? 1 : '' })}`),

  // --- cameras
  cameras: (params) => request(`/api/cameras${qs(params)}`),
  camera: (id) => request(`/api/cameras/${id}`),
  setCameraStatus: (id, status) =>
    request(`/api/cameras/${id}`, { method: 'PATCH', body: JSON.stringify({ status }) }),

  // --- analytics / network
  analytics: (params) => request(`/api/analytics${qs(params)}`),
  network: () => request('/api/network'),
  cities: () => request('/api/cities'),
  locations: (city) => request(`/api/locations${qs({ city })}`),

  // --- alerts
  alerts: (open) => request(`/api/alerts${qs({ open: open ? 1 : '' })}`),
  ackAlert: (id) => request(`/api/alerts/${id}/ack`, { method: 'POST' }),

  // --- search
  search: (q, city) => request(`/api/search${qs({ q, city })}`),

  // --- AI
  aiStatus: () => request('/api/ai/status'),
  aiHistory: () => request('/api/ai/chat'),
  aiChat: (message, context) =>
    request('/api/ai/chat', { method: 'POST', body: JSON.stringify({ message, context }) }),
  aiClear: () => request('/api/ai/chat', { method: 'DELETE' }),
  aiAnalyze: (formData) => request('/api/ai/analyze', { method: 'POST', body: formData }),

  // --- system
  systemStatus: () => request('/api/system/status'),
  setDemo: (enabled) =>
    request('/api/system/demo', { method: 'POST', body: JSON.stringify({ enabled }) }),
  settings: () => request('/api/system/settings'),
  saveSettings: (values) =>
    request('/api/system/settings', { method: 'POST', body: JSON.stringify(values) }),
};
