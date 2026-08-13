/**
 * Central application state.
 *
 * One object, one setter, one subscription list. Components never hold their
 * own copy of domain data — they read from here and re-render on the keys they
 * care about. That is what makes "backend changes -> UI reacts" hold for every
 * panel rather than only the ones we remembered to wire up.
 */

const listeners = new Set();

export const state = {
  // --- selection / navigation
  cityId: 'nyc',
  cities: [],
  locations: [],
  view: 'dashboard',
  mapMode: 'map',
  selectedSightingId: null,
  selectedCameraId: null,
  dockTab: 'cameras',

  // --- domain data
  sightings: [],
  sightingTotal: 0,
  sightingDetail: null,
  prediction: null,
  cameras: [],
  cameraCounts: {},
  analytics: null,
  analyticsRange: '24h',
  network: null,
  alerts: [],
  aiMessages: [],
  aiEngine: null,

  // --- system
  systemStatus: null,
  streamState: 'connecting',   // connecting | open | closed
  demoMode: false,
  serverVersion: '',

  // --- filters (applied to the map, feed, counters and analytics alike)
  filters: {
    window: null,        // minutes
    minConfidence: null,
    source: null,
    status: null,
    q: '',
  },

  // --- preferences
  prefs: {
    bootSequence: true,
    sound: false,
    reducedMotion: false,
  },

  // --- transient UI
  loading: {},
  errors: {},
};

let queued = null;

/**
 * Merge a patch into state and notify subscribers.
 *
 * Notifications are coalesced to one per frame: a single SSE event can touch
 * sightings, prediction, analytics and alerts, and re-rendering four times in
 * one tick is wasted work.
 */
export function set(patch, meta = {}) {
  const changed = [];
  for (const [key, value] of Object.entries(patch)) {
    if (state[key] !== value) changed.push(key);
    state[key] = value;
  }
  if (!changed.length && !meta.force) return;

  if (queued) {
    queued.keys.push(...changed);
    return;
  }
  queued = { keys: [...changed] };
  requestAnimationFrame(() => {
    const keys = new Set(queued.keys);
    queued = null;
    for (const fn of listeners) {
      try {
        fn(state, keys);
      } catch (err) {
        console.error('[store] subscriber failed', err);
      }
    }
  });
}

/** Patch a nested object key (filters, prefs, loading) without clobbering it. */
export function patch(key, values) {
  set({ [key]: { ...state[key], ...values } }, { force: true });
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** Subscribe only to specific top-level keys. */
export function watch(keys, fn) {
  const wanted = new Set(Array.isArray(keys) ? keys : [keys]);
  return subscribe((s, changed) => {
    for (const key of changed) {
      if (wanted.has(key)) { fn(s, changed); return; }
    }
  });
}

export function setLoading(key, value) {
  patch('loading', { [key]: value });
}

export function setError(key, message) {
  patch('errors', { [key]: message || null });
}

// --- derived selectors ----------------------------------------------------

export function activeCity() {
  return state.cities.find((c) => c.id === state.cityId) || null;
}

/**
 * Sightings after client-side filters.
 *
 * The server applies the same filters when fetching, but events arriving over
 * SSE bypass that query, so the predicate is enforced here too. One definition,
 * used by the map, the feed and the counters.
 */
export function visibleSightings() {
  const f = state.filters;
  const now = Date.now() / 1000;
  return state.sightings.filter((s) => {
    if (s.city_id !== state.cityId) return false;
    if (f.window && now - s.ts > f.window * 60) return false;
    if (f.minConfidence && s.confidence < f.minConfidence) return false;
    if (f.source && s.source !== f.source) return false;
    if (f.status) {
      const allowed = f.status.split(',');
      if (!allowed.includes(s.status)) return false;
    }
    if (f.q) {
      const q = f.q.toLowerCase();
      const hay = `${s.ref} ${s.area} ${s.description || ''} ${s.camera_id || ''}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

export function latestSighting() {
  const list = visibleSightings();
  return list.length ? list.reduce((a, b) => (b.ts > a.ts ? b : a)) : null;
}

export function selectedSighting() {
  if (!state.selectedSightingId) return null;
  return state.sightings.find((s) => s.id === state.selectedSightingId)
      || state.sightingDetail
      || null;
}

export function hasActiveFilters() {
  const f = state.filters;
  return Boolean(f.window || f.minConfidence || f.source || f.status || f.q);
}

/** Merge one sighting into the list, newest first, without duplicating. */
export function upsertSighting(sighting, limit = 400) {
  const list = state.sightings.slice();
  const index = list.findIndex((s) => s.id === sighting.id);
  if (index >= 0) list[index] = { ...list[index], ...sighting };
  else list.unshift(sighting);
  list.sort((a, b) => b.ts - a.ts);
  set({ sightings: list.slice(0, limit) });
}

export function removeSighting(id) {
  set({ sightings: state.sightings.filter((s) => s.id !== id) });
  if (state.selectedSightingId === id) {
    set({ selectedSightingId: null, sightingDetail: null });
  }
}
