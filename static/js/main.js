/**
 * Spidey Tracker — application entry point.
 *
 * Responsibilities: boot sequence, initial load, SSE wiring, navigation,
 * search, filters, keyboard shortcuts, preferences, and the action bus that
 * lets NED (and search results, and alerts) drive the dashboard.
 */

import {
  state, set, patch, watch, subscribe, activeCity, visibleSightings,
  latestSighting, selectedSighting, upsertSighting, removeSighting, hasActiveFilters,
} from './core/store.js';
import { api, filterParams, ApiError } from './core/api.js';
import * as stream from './core/events.js';
import * as sound from './core/sound.js';
import {
  el, mount, clear, esc, ago, agoFromTs, clockTime, debounce, num, emptyState,
} from './core/format.js';

import * as gmap from './components/map.js';
import * as panels from './components/panels.js';
import * as ned from './components/ned.js';
import * as dossier from './components/dossier.js';
import * as alerts from './components/alerts.js';

const PREF_KEY = 'spidey.prefs.v1';
const newSightingIds = new Set();

// ===========================================================================
// PREFERENCES
// ===========================================================================

function loadPrefs() {
  try {
    const raw = localStorage.getItem(PREF_KEY);
    if (raw) patch('prefs', JSON.parse(raw));
  } catch (err) {
    console.warn('[prefs] unreadable, using defaults', err);
  }
  applyPrefs();
}

function savePrefs() {
  try {
    localStorage.setItem(PREF_KEY, JSON.stringify(state.prefs));
  } catch (err) {
    console.warn('[prefs] could not persist', err);
  }
}

function applyPrefs() {
  document.documentElement.dataset.reducedMotion = state.prefs.reducedMotion ? '1' : '0';
  sound.setEnabled(state.prefs.sound);
}

// ===========================================================================
// BOOT SEQUENCE
// ===========================================================================

const BOOT_CHECKS = [
  ['MAP DATA', async () => { await waitForLeaflet(); return true; }],
  ['DATABASE', async () => (await api.systemStatus()).subsystems
      .find((s) => s.id === 'database')?.ok],
  ['SIGHTING NETWORK', async () => { await loadCities(); return true; }],
  ['CAMERA NETWORK', async () => { await loadCameras(); return true; }],
  ['PREDICTION ENGINE', async () => { await loadPrediction(); return true; }],
  ['AI ENGINE', async () => (await api.aiStatus()).ready],
  ['NED AI', async () => true],
];

function waitForLeaflet() {
  return new Promise((resolve, reject) => {
    if (window.L) return resolve(true);
    let waited = 0;
    const timer = setInterval(() => {
      if (window.L) { clearInterval(timer); resolve(true); }
      else if ((waited += 100) > 8000) {
        clearInterval(timer);
        reject(new Error('Leaflet failed to load — check the network.'));
      }
    }, 100);
  });
}

async function runBoot() {
  const boot = document.getElementById('boot');
  const lines = document.getElementById('boot-lines');
  const ready = document.getElementById('boot-ready');
  const skip = document.getElementById('boot-skip');
  const disable = document.getElementById('boot-disable');

  const skipSequence = !state.prefs.bootSequence;
  if (disable) disable.checked = !state.prefs.bootSequence;

  let skipped = skipSequence;
  const finish = () => {
    boot.classList.add('is-done');
    setTimeout(() => { boot.hidden = true; }, 520);
    document.getElementById('app').hidden = false;
    gmap.invalidate();
  };

  if (skip) {
    skip.addEventListener('click', () => { skipped = true; finish(); });
  }
  if (disable) {
    disable.addEventListener('change', () => {
      patch('prefs', { bootSequence: !disable.checked });
      savePrefs();
    });
  }

  sound.playBootChord();

  for (const [label, check] of BOOT_CHECKS) {
    const line = el('div.boot__line', {}, [
      el('span', { text: label }),
      el('span.boot__dots'),
      el('span', { text: '…' }),
    ]);
    if (!skipped) lines.appendChild(line);

    let ok = false;
    let detail = '';
    try {
      ok = Boolean(await check());
    } catch (err) {
      ok = false;
      detail = err.message;
    }

    line.lastChild.textContent = ok ? 'OK' : 'FAIL';
    line.lastChild.className = ok ? 'boot__ok' : 'boot__fail';
    if (!ok && detail) {
      line.title = detail;
      console.error(`[boot] ${label}: ${detail}`);
    }

    if (!skipped) await new Promise((r) => setTimeout(r, 130));
  }

  if (!skipped) {
    ready.hidden = false;
    sound.play('boot');
    await new Promise((r) => setTimeout(r, 620));
  }
  finish();
}

// ===========================================================================
// DATA LOADING
// ===========================================================================

async function loadCities() {
  const [cities, locations] = await Promise.all([api.cities(), api.locations()]);
  set({ cities: cities.cities, locations: locations.locations });

  const select = document.getElementById('city-select');
  if (select) {
    mount(select, cities.cities.map((c) => el('option', {
      value: c.id, text: c.name, selected: c.id === state.cityId,
    })));
  }
  return cities.cities;
}

async function loadSightings() {
  try {
    const params = { ...filterParams(state.filters, state.cityId), limit: 300 };
    const data = await api.sightings(params);
    set({ sightings: data.sightings, sightingTotal: data.total });
  } catch (err) {
    console.error('[load] sightings', err);
    toast('SIGHTING LOAD FAILED', err.message, 'warning');
  }
}

async function loadPrediction(refresh = false) {
  try {
    const prediction = await api.prediction(state.cityId, refresh);
    set({ prediction });
  } catch (err) {
    console.error('[load] prediction', err);
  }
}

async function loadCameras() {
  try {
    const data = await api.cameras({ city: state.cityId });
    set({ cameras: data.cameras, cameraCounts: data.counts });
  } catch (err) {
    console.error('[load] cameras', err);
  }
}

async function loadAnalytics() {
  try {
    const data = await api.analytics({ city: state.cityId, range: state.analyticsRange });
    set({ analytics: data });
  } catch (err) {
    console.error('[load] analytics', err);
  }
}

async function loadNetwork() {
  try {
    set({ network: await api.network() });
  } catch (err) {
    console.error('[load] network', err);
  }
}

async function loadSystem() {
  try {
    const status = await api.systemStatus();
    set({ systemStatus: status, demoMode: status.demo_mode, serverVersion: status.version });
  } catch (err) {
    set({ systemStatus: null });
  }
}

const refreshAll = debounce(async () => {
  await Promise.all([
    loadSightings(), loadPrediction(), loadCameras(), loadAnalytics(), loadNetwork(),
  ]);
}, 200);

// ===========================================================================
// SSE WIRING — this is what makes the console live
// ===========================================================================

function wireStream() {
  stream.on('stream.state', (value) => {
    set({ streamState: value });
    renderSystemIndicator();
  });

  stream.on('stream.connected', () => {
    set({ streamState: 'open' });
    renderSystemIndicator();
  });

  stream.on('sighting.created', (sighting) => {
    if (!sighting || !sighting.id) return;
    upsertSighting(sighting);

    if (sighting.city_id === state.cityId) {
      newSightingIds.add(sighting.id);
      setTimeout(() => newSightingIds.delete(sighting.id), 4000);
      gmap.pulseNew(sighting);
      sound.play('new');
    }
    scheduleAnalyticsRefresh();
  });

  stream.on('sighting.updated', (sighting) => {
    if (!sighting || !sighting.id) return;
    upsertSighting(sighting);
    if (state.selectedSightingId === sighting.id) set({ sightingDetail: sighting });
  });

  stream.on('sighting.deleted', (payload) => {
    removeSighting(payload.id);
    scheduleAnalyticsRefresh();
  });

  stream.on('prediction.updated', (prediction) => {
    if (prediction && prediction.city_id === state.cityId) set({ prediction });
  });

  stream.on('camera.detected', (payload) => {
    const cameras = state.cameras.map((c) => (c.id === payload.camera_id
      ? {
          ...c,
          status: 'detected',
          last_detection: {
            ts: payload.ts, confidence: payload.confidence,
            sighting_id: payload.sighting_id, age_sec: 0,
            is_demo: payload.is_demo,
          },
        }
      : c));
    set({ cameras });
    sound.play('ping');
  });

  stream.on('camera.status_changed', (payload) => {
    set({
      cameras: state.cameras.map((c) => (c.id === payload.camera_id
        ? { ...c, status: payload.status, last_status_at: payload.ts, status_age_sec: 0 }
        : c)),
    });
  });

  stream.on('alert.created', (payload) => {
    const sighting = payload.sighting || {};
    // Only surface alerts for the city being watched; others still land in the
    // network counters but must not interrupt the operator.
    if (sighting.city_id && sighting.city_id !== state.cityId) return;
    alerts.fromEvent(payload);
    set({ alerts: [payload, ...state.alerts].slice(0, 40) });
  });

  stream.on('network.updated', () => scheduleNetworkRefresh());

  stream.on('system.status_changed', (payload) => {
    if (payload && typeof payload.demo_mode === 'boolean') {
      set({ demoMode: payload.demo_mode });
    }
    scheduleSystemRefresh();
  });

  stream.connect();
}

const scheduleAnalyticsRefresh = debounce(() => loadAnalytics(), 2500);
const scheduleNetworkRefresh = debounce(() => loadNetwork(), 3000);
const scheduleSystemRefresh = debounce(() => loadSystem(), 1200);

// ===========================================================================
// ACTION BUS — NED, search results and alerts all route through here
// ===========================================================================

function runAction(action) {
  if (!action || !action.type) return;
  switch (action.type) {
    case 'map.focus':
      if (action.latitude !== undefined) {
        gmap.focus(action.latitude, action.longitude, action.zoom || 15);
      }
      break;

    case 'sighting.open':
      selectSighting(action.sighting_id);
      break;

    case 'camera.open':
      openCamera(action.camera_id);
      break;

    case 'panel.open':
      openView(action.panel);
      break;

    case 'map.mode':
      setMapMode(action.mode);
      break;

    case 'city.set':
      setCity(action.city_id);
      break;

    case 'filter.set': {
      const next = {};
      if (action.min_confidence !== undefined) next.minConfidence = action.min_confidence;
      if (action.window_minutes !== undefined) next.window = action.window_minutes;
      if (action.source !== undefined) next.source = action.source;
      patch('filters', next);
      syncFilterButtons();
      loadSightings();
      break;
    }

    default:
      console.warn('[action] unknown type', action.type);
  }
}

// ===========================================================================
// NAVIGATION / SELECTION
// ===========================================================================

async function selectSighting(id) {
  if (!id) {
    set({ selectedSightingId: null, sightingDetail: null });
    gmap.render();
    return;
  }

  set({ selectedSightingId: id });
  gmap.render();

  const known = state.sightings.find((s) => s.id === id);
  if (known) gmap.focus(known.latitude, known.longitude, Math.max(14, gmap.getMap().getZoom()));

  try {
    const detail = await api.sighting(id);
    set({ sightingDetail: detail });
    openOverlay(`SIGHTING ${detail.ref}`, (body) => dossier.renderDossier(body, detail));
    gmap.focus(detail.latitude, detail.longitude, Math.max(15, gmap.getMap().getZoom()));
  } catch (err) {
    toast('COULD NOT LOAD SIGHTING', err.message, 'warning');
  }
}

async function openCamera(cameraId) {
  try {
    const camera = await api.camera(cameraId);
    set({ selectedCameraId: cameraId });
    openOverlay(`CAMERA ${camera.id}`, (body) => dossier.renderCameraDetail(body, camera));
    gmap.focus(camera.latitude, camera.longitude, 15);
  } catch (err) {
    toast('CAMERA UNAVAILABLE', err.message, 'warning');
  }
}

function setCity(cityId) {
  if (!cityId || cityId === state.cityId) return;
  const city = state.cities.find((c) => c.id === cityId);
  set({ cityId, selectedSightingId: null, sightingDetail: null, prediction: null });

  const select = document.getElementById('city-select');
  if (select) select.value = cityId;

  gmap.clearSeen();
  if (city) gmap.fitCity(city);
  refreshAll();
  sound.play('click');
}

function setMapMode(mode) {
  if (!mode) return;
  set({ mapMode: mode });
  gmap.setMode(mode);
  document.querySelectorAll('[data-mode]').forEach((btn) => {
    btn.classList.toggle('btn--active', btn.dataset.mode === mode);
  });
  // The dock's radar tab mirrors the map's radar mode.
  if (mode === 'radar') setDockTab('radar');
  sound.play('click');
}

// ===========================================================================
// OVERLAY / VIEWS
// ===========================================================================

let overlayRenderer = null;

function openOverlay(title, renderer, meta = '') {
  const overlay = document.getElementById('overlay');
  const titleNode = document.getElementById('overlay-title');
  const metaNode = document.getElementById('overlay-meta');
  const body = document.getElementById('overlay-body');
  if (!overlay) return;

  titleNode.textContent = title;
  metaNode.textContent = meta;
  overlayRenderer = renderer;
  renderer(body);
  overlay.hidden = false;
  document.getElementById('overlay-close')?.focus();
}

function closeOverlay() {
  const overlay = document.getElementById('overlay');
  if (!overlay || overlay.hidden) return;
  overlay.hidden = true;
  overlayRenderer = null;
  set({ view: 'dashboard' });
  document.querySelectorAll('.navbtn').forEach((btn) => {
    btn.classList.toggle('is-active', btn.dataset.view === 'dashboard');
  });
}

function refreshOverlay() {
  const overlay = document.getElementById('overlay');
  if (!overlay || overlay.hidden || !overlayRenderer) return;
  overlayRenderer(document.getElementById('overlay-body'));
}

function openView(view) {
  set({ view });
  document.querySelectorAll('.navbtn').forEach((btn) => {
    btn.classList.toggle('is-active', btn.dataset.view === view);
  });

  switch (view) {
    case 'dashboard':
      closeOverlay();
      break;

    case 'feed':
      openOverlay('LIVE FEED — ALL SIGHTINGS', (body) => {
        const list = visibleSightings();
        mount(body, [
          el('div', { style: { display: 'flex', gap: 'var(--sp-4)', alignItems: 'center',
                               marginBottom: 'var(--sp-5)', flexWrap: 'wrap' } }, [
            el('span.px-sm.t-cyan', { text: `${list.length} SIGHTINGS` }),
            hasActiveFilters()
              ? el('span.badge.badge--orange', {}, ['FILTERED'])
              : null,
            el('button.btn.btn--sm', {
              type: 'button', style: { marginLeft: 'auto' },
              onclick: () => { gmap.fitSightings(list); closeOverlay(); },
            }, ['FIT MAP TO RESULTS']),
          ]),
          list.length
            ? el('div.feed', {}, list.map((s) => panels.feedItem(s)))
            : emptyState('NOTHING MATCHES THE CURRENT FILTERS'),
        ]);
      });
      break;

    case 'prediction':
      setMapMode('prediction');
      openOverlay('PREDICTION ENGINE', (body) => {
        const container = el('div');
        mount(body, container);
        const holder = el('div', { id: 'pred-overlay' });
        container.appendChild(holder);
        renderPredictionOverlay(holder);
      });
      break;

    case 'cameras':
      openOverlay('CAMERA NETWORK', (body) => {
        const holder = el('div');
        mount(body, holder);
        panels.renderCameras(holder, 999);
      });
      break;

    case 'analytics':
      openOverlay('ACTIVITY ANALYTICS', (body) => {
        const holder = el('div');
        mount(body, holder);
        panels.renderAnalytics(holder, { full: true });
      });
      break;

    case 'network':
      openOverlay('GLOBAL SIGHTING NETWORK', (body) => {
        const holder = el('div');
        mount(body, holder);
        panels.renderNetwork(holder);
      });
      break;

    case 'report':
      openOverlay('REPORT A SIGHTING', (body) => dossier.renderReportForm(body));
      break;

    default:
      break;
  }
  sound.play('click');
}

function renderPredictionOverlay(holder) {
  const prediction = state.prediction;
  if (!prediction || !prediction.candidates.length) {
    mount(holder, emptyState('NO PREDICTION AVAILABLE',
                             'THE ENGINE NEEDS AT LEAST TWO RECENT SIGHTINGS'));
    return;
  }

  const vector = prediction.vector;
  mount(holder, [
    el('div', { style: { display: 'grid', gap: 'var(--sp-2)', marginBottom: 'var(--sp-5)',
                         gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))' } }, [
      el('div.stat', {}, [
        el('div.stat__label', { text: 'LEAD CANDIDATE' }),
        el('div.stat__value.stat__value--purple', { text: `${Math.round(prediction.candidates[0].probability)}%` }),
        el('div.stat__sub', { text: prediction.candidates[0].name }),
      ]),
      el('div.stat', {}, [
        el('div.stat__label', { text: 'ETA WINDOW' }),
        el('div.stat__value', { text: prediction.eta_min !== null
          ? `${Math.round(prediction.eta_min)}-${Math.round(prediction.eta_max)}` : '—' }),
        el('div.stat__sub', { text: 'MINUTES' }),
      ]),
      el('div.stat', {}, [
        el('div.stat__label', { text: 'CONFIDENCE' }),
        el('div.stat__value.stat__value--green', { text: `${Math.round(prediction.confidence)}%` }),
        el('div.stat__sub', { text: `${prediction.samples} SAMPLES` }),
      ]),
      el('div.stat', {}, [
        el('div.stat__label', { text: 'VECTOR' }),
        el('div.stat__value', { text: vector ? vector.compass : '—' }),
        el('div.stat__sub', { text: vector ? `${Math.round(vector.speed_kmh)} KM/H` : 'NO SIGNAL' }),
      ]),
    ]),

    el('div.px-sm.t-purple', { style: { marginBottom: 'var(--sp-3)' }, text: 'CANDIDATE DESTINATIONS' }),
    ...prediction.candidates.map((candidate) => {
      const track = el('div.bar__track', {}, [
        el('div.bar__fill.bar__fill--purple', { style: { width: '0%' } }),
      ]);
      requestAnimationFrame(() => { track.firstChild.style.width = `${candidate.probability}%`; });
      return el('button.pred__row', {
        type: 'button',
        onclick: () => {
          gmap.focus(candidate.latitude, candidate.longitude, 14);
          closeOverlay();
        },
      }, [
        el('div.pred__row-name', { text: candidate.name }),
        el('div.bar__value', { text: `${Math.round(candidate.probability)}%` }),
        el('div.pred__row-bar', {}, [track]),
        el('div.px-xs.dim', { style: { gridColumn: '1 / -1' },
          text: `${candidate.distance_km} KM · ETA ${Math.round(candidate.eta_min)} MIN` }),
      ]);
    }),

    el('div.dossier__notes', { style: { marginTop: 'var(--sp-6)' },
      text: `Method: ${prediction.method}. Computed from ${prediction.samples} sightings in the `
          + `last ${Math.round(state.prediction.projection ? 180 : 180)} minutes. `
          + 'Probabilities are derived from observed movement, not assigned.' }),
  ]);
}

// ===========================================================================
// DOCK TABS
// ===========================================================================

function setDockTab(tab) {
  set({ dockTab: tab });
  document.querySelectorAll('.tab').forEach((btn) => {
    btn.setAttribute('aria-selected', String(btn.dataset.tab === tab));
  });
  renderDockTab();
}

function renderDockTab() {
  const body = document.getElementById('dock-tab-body');
  if (!body) return;
  switch (state.dockTab) {
    case 'cameras':   panels.renderCameras(body, 8); break;
    case 'analytics': panels.renderAnalytics(body, { full: false }); break;
    case 'radar':     panels.renderRadar(body); break;
    case 'system':    panels.renderSystem(body); break;
    default: break;
  }
}

// ===========================================================================
// SEARCH
// ===========================================================================

function wireSearch() {
  const input = document.getElementById('search-input');
  const results = document.getElementById('search-results');
  if (!input || !results) return;

  let cursor = -1;
  let items = [];

  const close = () => {
    results.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    cursor = -1;
  };

  const run = debounce(async () => {
    const term = input.value.trim();
    if (term.length < 2) { close(); return; }

    try {
      const data = await api.search(term, state.cityId);
      items = [];
      if (!data.groups.length) {
        mount(results, [el('div.empty', {}, [`NO MATCHES FOR "${term.toUpperCase()}"`])]);
      } else {
        const nodes = [];
        for (const group of data.groups) {
          nodes.push(el('div.results__group-label', { text: group.label }));
          for (const item of group.items) {
            const node = el('button.results__item', {
              type: 'button',
              onclick: () => {
                if (item.city_id && item.city_id !== state.cityId
                    && item.action.type !== 'city.set') {
                  setCity(item.city_id);
                }
                runAction(item.action);
                close();
                input.value = '';
              },
            }, [
              el('div', {}, [
                el('div.results__title', { text: item.title }),
                el('div.results__sub', { text: item.subtitle || '' }),
              ]),
              el('div.results__meta', { text: item.meta || '' }),
            ]);
            items.push(node);
            nodes.push(node);
          }
        }
        mount(results, nodes);
      }
      results.hidden = false;
      input.setAttribute('aria-expanded', 'true');
    } catch (err) {
      mount(results, [el('div.empty', {}, [`SEARCH FAILED: ${err.message}`])]);
      results.hidden = false;
    }
  }, 220);

  input.addEventListener('input', run);
  input.addEventListener('focus', () => { if (input.value.trim().length >= 2) run(); });

  input.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { close(); input.blur(); return; }
    if (!items.length || results.hidden) return;

    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      items[cursor]?.classList.remove('is-cursor');
      cursor = event.key === 'ArrowDown'
        ? (cursor + 1) % items.length
        : (cursor - 1 + items.length) % items.length;
      items[cursor].classList.add('is-cursor');
      items[cursor].scrollIntoView({ block: 'nearest' });
    } else if (event.key === 'Enter' && cursor >= 0) {
      event.preventDefault();
      items[cursor].click();
    }
  });

  document.addEventListener('click', (event) => {
    if (!results.contains(event.target) && event.target !== input) close();
  });
}

// ===========================================================================
// FILTERS
// ===========================================================================

function wireFilters() {
  document.querySelectorAll('[data-filter-window]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const value = btn.dataset.filterWindow;
      patch('filters', { window: value ? Number(value) : null });
      syncFilterButtons();
      loadSightings();
      sound.play('click');
    });
  });

  const toggle = (attr, key, cast = (v) => v) => {
    document.querySelectorAll(`[data-filter-${attr}]`).forEach((btn) => {
      btn.addEventListener('click', () => {
        const raw = btn.dataset[`filter${attr[0].toUpperCase()}${attr.slice(1)}`];
        const value = cast(raw);
        patch('filters', { [key]: state.filters[key] === value ? null : value });
        syncFilterButtons();
        loadSightings();
        sound.play('click');
      });
    });
  };

  toggle('conf', 'minConfidence', Number);
  toggle('source', 'source');
  toggle('status', 'status');

  document.getElementById('filters-clear')?.addEventListener('click', () => {
    patch('filters', { window: null, minConfidence: null, source: null, status: null, q: '' });
    syncFilterButtons();
    loadSightings();
    sound.play('click');
  });
}

function syncFilterButtons() {
  const f = state.filters;
  document.querySelectorAll('[data-filter-window]').forEach((btn) => {
    const value = btn.dataset.filterWindow ? Number(btn.dataset.filterWindow) : null;
    btn.classList.toggle('btn--active', f.window === value);
  });
  document.querySelectorAll('[data-filter-conf]').forEach((btn) => {
    btn.classList.toggle('btn--active', f.minConfidence === Number(btn.dataset.filterConf));
  });
  document.querySelectorAll('[data-filter-source]').forEach((btn) => {
    btn.classList.toggle('btn--active', f.source === btn.dataset.filterSource);
  });
  document.querySelectorAll('[data-filter-status]').forEach((btn) => {
    btn.classList.toggle('btn--active', f.status === btn.dataset.filterStatus);
  });
}

// ===========================================================================
// MOBILE
// ===========================================================================

function wireMobile() {
  const sheet = document.getElementById('sheet');
  const sheetBody = document.getElementById('sheet-body');
  const sheetTitle = document.getElementById('sheet-title');

  const closeSheet = () => {
    sheet.classList.remove('is-open');
    sheet.setAttribute('aria-hidden', 'true');
    document.querySelectorAll('.mobilebar__btn').forEach((b) => {
      b.classList.toggle('is-active', b.dataset.sheet === '');
    });
  };

  document.getElementById('sheet-close')?.addEventListener('click', closeSheet);

  document.querySelectorAll('.mobilebar__btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const which = btn.dataset.sheet;
      document.querySelectorAll('.mobilebar__btn').forEach((b) => {
        b.classList.toggle('is-active', b === btn);
      });

      if (!which) { closeSheet(); return; }

      if (which === 'menu') {
        closeSheet();
        openView('analytics');
        return;
      }

      sheetTitle.textContent = which.toUpperCase();
      sheet.classList.add('is-open');
      sheet.setAttribute('aria-hidden', 'false');

      if (which === 'feed') {
        const list = visibleSightings();
        mount(sheetBody, list.length
          ? el('div.feed', {}, list.slice(0, 60).map((s) => panels.feedItem(s)))
          : emptyState('NO ACTIVITY'));
      } else if (which === 'prediction') {
        const holder = el('div', { style: { padding: 'var(--sp-4)' } });
        mount(sheetBody, holder);
        renderPredictionOverlay(holder);
      } else if (which === 'ned') {
        // Move the live NED panel into the sheet rather than duplicating it,
        // so there is only ever one chat log and one set of listeners.
        const nedPanel = document.querySelector('.ned');
        if (nedPanel) {
          mount(sheetBody, nedPanel);
          nedPanel.style.height = '100%';
        }
      }
      sound.play('click');
    });
  });

  // Swipe down on the grip closes the sheet.
  let startY = null;
  const grip = document.getElementById('sheet-grip');
  grip?.addEventListener('touchstart', (e) => { startY = e.touches[0].clientY; }, { passive: true });
  grip?.addEventListener('touchmove', (e) => {
    if (startY === null) return;
    if (e.touches[0].clientY - startY > 60) { closeSheet(); startY = null; }
  }, { passive: true });
}

function renderMobileStatus() {
  const node = document.getElementById('mobile-status');
  if (!node) return;
  const latest = latestSighting();
  if (!latest) { mount(node, el('div.px-xs.dim', { text: 'NO SIGHTINGS' })); return; }

  mount(node, el('button', {
    type: 'button',
    style: { background: 'none', border: 0, width: '100%', textAlign: 'left',
             color: 'inherit', fontFamily: 'inherit', cursor: 'pointer', padding: 0 },
    onclick: () => selectSighting(latest.id),
  }, [
    el('div', { style: { display: 'flex', alignItems: 'baseline', gap: 'var(--sp-3)' } }, [
      el('span.px-xs.dim', { text: 'LAST SIGHTING' }),
      el('span.px-xs', { style: { marginLeft: 'auto', color: 'var(--cyan)' },
                         text: agoFromTs(latest.ts) }),
    ]),
    el('div', { style: { display: 'flex', alignItems: 'baseline', gap: 'var(--sp-4)',
                         marginTop: '2px' } }, [
      el('span.px-sm', { text: latest.area }),
      el('span.term-lg', { style: { marginLeft: 'auto' },
                           text: `${Math.round(latest.confidence)}%` }),
    ]),
  ]));
}

// ===========================================================================
// HEADER / SYSTEM INDICATOR
// ===========================================================================

function renderSystemIndicator() {
  const dot = document.querySelector('#sys-indicator .dot');
  const text = document.getElementById('sys-text');
  const wrap = document.getElementById('sys-indicator');
  if (!text || !wrap) return;

  const map = {
    open: ['SYSTEM ONLINE', '', 'var(--green)'],
    connecting: ['CONNECTING', 'sysline--warn', 'var(--orange)'],
    closed: ['NETWORK OFFLINE', 'sysline--err', 'var(--red)'],
  };
  const [label, cls, color] = map[state.streamState] || map.closed;

  text.textContent = state.demoMode && state.streamState === 'open'
    ? 'ONLINE · DEMO'
    : label;
  wrap.className = `sysline ${cls}`;
  if (dot) dot.style.color = color;
}

function startClock() {
  const node = document.getElementById('clock');
  const tick = () => { if (node) node.textContent = clockTime(); };
  tick();
  setInterval(tick, 1000);
}

// ===========================================================================
// TOASTS
// ===========================================================================

function toast(title, body, severity = 'info') {
  alerts.push({ title, body, severity });
}

// ===========================================================================
// KEYBOARD
// ===========================================================================

function wireKeyboard() {
  document.addEventListener('keydown', (event) => {
    const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target.tagName);

    if (event.key === 'Escape') {
      closeOverlay();
      return;
    }

    if (typing) return;

    if (event.key === '/') { event.preventDefault(); document.getElementById('search-input')?.focus(); return; }

    const viewKeys = { 1: 'dashboard', 2: 'feed', 3: 'prediction', 4: 'cameras', 5: 'analytics', 6: 'network', r: 'report' };
    if (viewKeys[event.key]) { openView(viewKeys[event.key]); return; }

    const modeKeys = { m: 'map', s: 'satellite', t: 'terrain', h: 'heatmap', p: 'prediction', d: 'radar' };
    if (modeKeys[event.key]) { setMapMode(modeKeys[event.key]); return; }

    if (event.key === 'n') { document.getElementById('ned-input')?.focus(); }
  });
}

// ===========================================================================
// CUSTOM EVENT WIRING (components -> app)
// ===========================================================================

function wireCustomEvents() {
  window.addEventListener('spidey:select', (e) => selectSighting(e.detail));
  window.addEventListener('spidey:camera-open', (e) => openCamera(e.detail));
  window.addEventListener('spidey:action', (e) => runAction(e.detail));
  window.addEventListener('spidey:set-city', (e) => setCity(e.detail));
  window.addEventListener('spidey:close-overlay', () => closeOverlay());

  window.addEventListener('spidey:focus-point', (e) => {
    gmap.focus(e.detail.lat, e.detail.lon, e.detail.zoom || 15);
  });

  window.addEventListener('spidey:focus-area', (e) => {
    const match = state.sightings.find((s) => s.area === e.detail);
    if (match) gmap.focus(match.latitude, match.longitude, 14);
  });

  window.addEventListener('spidey:refresh-prediction', async () => {
    await loadPrediction(true);
    sound.play('ping');
  });

  window.addEventListener('spidey:set-range', (e) => {
    set({ analyticsRange: e.detail });
    loadAnalytics().then(refreshOverlay);
  });

  window.addEventListener('spidey:sighting-updated', (e) => {
    upsertSighting(e.detail);
    set({ sightingDetail: e.detail });
    refreshOverlay();
  });

  window.addEventListener('spidey:toast', (e) => {
    alerts.push({
      title: e.detail.title, body: e.detail.body,
      severity: e.detail.severity || 'info',
      sightingId: e.detail.sightingId || null,
    });
  });

  window.addEventListener('spidey:toggle-demo', async () => {
    try {
      const status = await api.setDemo(!state.demoMode);
      set({ demoMode: status.enabled });
      toast('DEMO NETWORK', status.enabled
        ? 'Simulated events resumed. All generated records are badged DEMO.'
        : 'Simulator stopped. Only real reports will appear.', 'info');
      loadSystem();
    } catch (err) {
      toast('DEMO TOGGLE FAILED', err.message, 'warning');
    }
  });

  window.addEventListener('spidey:toggle-sound', () => {
    patch('prefs', { sound: !state.prefs.sound });
    applyPrefs();
    savePrefs();
    if (state.prefs.sound) sound.play('ping');
    renderDockTab();
  });

  window.addEventListener('spidey:toggle-motion', () => {
    patch('prefs', { reducedMotion: !state.prefs.reducedMotion });
    applyPrefs();
    savePrefs();
    renderDockTab();
  });

  window.addEventListener('spidey:toggle-boot', () => {
    patch('prefs', { bootSequence: !state.prefs.bootSequence });
    savePrefs();
    renderDockTab();
  });

  // Report form asks the map for a coordinate.
  window.addEventListener('spidey:pick-position', (e) => {
    const callback = e.detail;
    closeOverlay();
    toast('PICK A POSITION', 'Click anywhere on the map to set the report location.', 'info');
    const map = gmap.getMap();
    const handler = (event) => {
      map.off('click', handler);
      callback(event.latlng.lat, event.latlng.lng);
      openView('report');
      setTimeout(() => {
        toast('POSITION SET', `${event.latlng.lat.toFixed(5)}, ${event.latlng.lng.toFixed(5)}`, 'info');
      }, 200);
    };
    map.on('click', handler);
  });
}

// ===========================================================================
// RENDER LOOP
// ===========================================================================

function wireRendering() {
  // Panels re-render only for the state keys they actually depend on.
  watch(['sightings', 'filters', 'cityId', 'selectedSightingId'], () => {
    panels.renderStatus();
    panels.renderFeed(40, newSightingIds);
    panels.renderArea();
    gmap.render();
    renderMobileStatus();
    if (state.dockTab === 'radar') renderDockTab();
  });

  watch(['prediction'], () => {
    panels.renderPrediction();
    gmap.render();
    if (state.view === 'prediction') refreshOverlay();
  });

  watch(['cameras'], () => {
    if (state.dockTab === 'cameras') renderDockTab();
    if (state.view === 'cameras') refreshOverlay();
    gmap.render();
  });

  watch(['analytics', 'analyticsRange'], () => {
    if (state.dockTab === 'analytics') renderDockTab();
  });

  watch(['network', 'systemStatus', 'streamState', 'demoMode'], () => {
    panels.renderTicker();
    renderSystemIndicator();
    if (state.dockTab === 'system') renderDockTab();
    if (state.view === 'network') refreshOverlay();
  });

  watch(['aiMessages'], () => ned.renderNed());

  // Relative timestamps go stale silently; refresh them on a slow tick.
  setInterval(() => {
    panels.renderStatus();
    panels.renderFeed(40, newSightingIds);
    renderMobileStatus();
    if (state.dockTab === 'cameras' || state.dockTab === 'system') renderDockTab();
  }, 20000);

  // System status is polled slowly as a safety net; SSE is the primary path.
  setInterval(loadSystem, 30000);
}

// ===========================================================================
// STARTUP
// ===========================================================================

async function start() {
  loadPrefs();
  alerts.initAlerts();
  startClock();

  // Sound requires a user gesture before the audio context can start.
  const unlock = () => { sound.unlock(); window.removeEventListener('pointerdown', unlock); };
  window.addEventListener('pointerdown', unlock, { once: true });

  wireCustomEvents();
  wireKeyboard();
  wireSearch();
  wireFilters();
  wireMobile();
  wireRendering();

  // Header + map controls
  document.querySelectorAll('.navbtn').forEach((btn) => {
    btn.addEventListener('click', () => openView(btn.dataset.view));
  });
  document.querySelectorAll('[data-mode]').forEach((btn) => {
    btn.addEventListener('click', () => setMapMode(btn.dataset.mode));
  });
  document.querySelectorAll('.tab').forEach((btn) => {
    btn.addEventListener('click', () => setDockTab(btn.dataset.tab));
  });
  document.querySelectorAll('[data-view]').forEach((btn) => {
    if (!btn.classList.contains('navbtn')) {
      btn.addEventListener('click', () => openView(btn.dataset.view));
    }
  });
  document.getElementById('city-select')?.addEventListener('change', (e) => setCity(e.target.value));
  document.getElementById('overlay-close')?.addEventListener('click', closeOverlay);
  document.getElementById('overlay')?.addEventListener('click', (e) => {
    if (e.target.id === 'overlay') closeOverlay();
  });

  document.getElementById('map-recenter')?.addEventListener('click', () => {
    const city = activeCity();
    if (city) gmap.fitCity(city);
  });

  document.getElementById('map-last')?.addEventListener('click', () => {
    const latest = latestSighting();
    if (latest) selectSighting(latest.id);
    else toast('NO SIGHTINGS', 'Nothing matches the current filters.', 'warning');
  });

  document.getElementById('map-locate')?.addEventListener('click', () => {
    if (!navigator.geolocation) {
      toast('UNAVAILABLE', 'This browser does not expose geolocation.', 'warning');
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => gmap.focus(pos.coords.latitude, pos.coords.longitude, 14),
      (err) => toast('LOCATION DENIED', err.message, 'warning'),
    );
  });

  // Boot runs the initial data load through its checks.
  await runBoot();

  // Map must be created after the shell is visible or Leaflet mis-measures it.
  // A map failure must not stop the rest of the console from coming up.
  const city = activeCity();
  try {
    gmap.initMap('map-canvas', {
      onSelect: (id) => selectSighting(id),
      center: city ? [city.latitude, city.longitude] : undefined,
      zoom: city ? city.default_zoom : undefined,
    });
    gmap.invalidate();
  } catch (err) {
    console.error('[startup] map unavailable', err);
  }

  if (!gmap.isAvailable()) {
    alerts.push({
      title: 'MAP UNAVAILABLE',
      body: 'Leaflet could not be loaded. Every other panel is still live.',
      severity: 'warning',
    });
  }

  await Promise.all([loadSightings(), loadAnalytics(), loadNetwork(), loadSystem()]);

  ned.initNed();
  wireStream();

  setDockTab('cameras');
  panels.renderStatus();
  panels.renderFeed();
  panels.renderArea();
  panels.renderPrediction();
  panels.renderTicker();
  renderSystemIndicator();
  renderMobileStatus();
  gmap.render();

  window.addEventListener('resize', debounce(() => {
    gmap.invalidate();
    gmap.render();
  }, 220));

  console.info(
    '%cSPIDEY TRACKER%c v' + (state.serverVersion || '') +
    ' — fictional demo platform. All sighting data is simulated.',
    'color:#FF3038;font-weight:bold', 'color:#6D9EAD',
  );
}

/**
 * A failure anywhere in startup used to abort every step after it, leaving a
 * dashboard that looked loaded but was inert. Surface it instead: reveal the
 * shell regardless, and say what broke.
 */
async function boot() {
  try {
    await start();
  } catch (err) {
    console.error('[startup] failed', err);
    const bootScreen = document.getElementById('boot');
    if (bootScreen) bootScreen.hidden = true;
    const app = document.getElementById('app');
    if (app) app.hidden = false;
    alerts.initAlerts();
    alerts.push({
      title: 'STARTUP INCOMPLETE',
      body: err && err.message ? err.message : String(err),
      severity: 'critical',
    });
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
