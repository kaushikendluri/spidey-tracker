/**
 * PixelMap — the live interactive map.
 *
 * Leaflet with custom pixel divIcons rather than default pins, a viewport-aware
 * marker budget, a hand-rolled grid clusterer, a canvas heatmap, and the
 * prediction layer (route, uncertainty zone, candidate diamonds).
 *
 * Mapbox would need an access token that this deployment does not have, so the
 * base layers are keyless: CARTO dark for MAP, Esri World Imagery for SATELLITE
 * and OpenTopoMap for TERRAIN.
 */

import { state, set, visibleSightings, selectedSighting } from '../core/store.js';
import { el, confidenceTone, esc, agoFromTs } from '../core/format.js';
import * as sound from '../core/sound.js';

const BASE_LAYERS = {
  map: {
    url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
    attribution: '&copy; OpenStreetMap &copy; CARTO',
    maxZoom: 19,
  },
  satellite: {
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Imagery &copy; Esri',
    maxZoom: 18,
  },
  terrain: {
    url: 'https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
    attribution: '&copy; OpenTopoMap (CC-BY-SA)',
    maxZoom: 16,
  },
};

// Above this many visible sightings we cluster instead of drawing every marker.
const CLUSTER_THRESHOLD = 60;
const CLUSTER_GRID_PX = 54;
const MAX_MARKERS = 320;

let map = null;
let baseLayer = null;
let panes = {};
let markerIndex = new Map();      // sighting id -> leaflet marker
let clusterLayers = [];
let cameraMarkers = new Map();
let predictionLayers = [];
let heatCanvas = null;
let heatLayer = null;
let seenIds = new Set();
let onSelect = () => {};
let onMove = () => {};
let programmaticMove = false;

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

export function initMap(containerId, handlers = {}) {
  onSelect = handlers.onSelect || (() => {});
  onMove = handlers.onMove || (() => {});

  map = L.map(containerId, {
    zoomControl: false,
    attributionControl: true,
    preferCanvas: true,
    worldCopyJump: true,
    minZoom: 2,
    zoomSnap: 0.5,
    wheelPxPerZoomLevel: 110,
  });

  // Dedicated panes so z-order between layers is explicit and stable.
  panes.heat = map.createPane('heat');       panes.heat.style.zIndex = 350;
  panes.zone = map.createPane('zone');       panes.zone.style.zIndex = 380;
  panes.route = map.createPane('route');     panes.route.style.zIndex = 400;
  panes.cameras = map.createPane('cameras'); panes.cameras.style.zIndex = 430;
  panes.markers = map.createPane('marks');   panes.markers.style.zIndex = 460;

  setBaseLayer('map');

  map.on('moveend zoomend', () => {
    render();
    if (!programmaticMove) onMove(map.getCenter(), map.getZoom());
  });

  map.on('click', () => {
    if (state.selectedSightingId) onSelect(null);
  });

  return map;
}

export function getMap() {
  return map;
}

function setBaseLayer(mode) {
  const config = BASE_LAYERS[mode] || BASE_LAYERS.map;
  if (baseLayer) map.removeLayer(baseLayer);
  baseLayer = L.tileLayer(config.url, {
    attribution: `${config.attribution} &middot; SIMULATED SIGHTING DATA`,
    maxZoom: config.maxZoom,
    subdomains: 'abcd',
    crossOrigin: true,
  });
  baseLayer.addTo(map);
}

// ---------------------------------------------------------------------------
// View control
// ---------------------------------------------------------------------------

export function focus(lat, lon, zoom, { animate = true } = {}) {
  if (!map) return;
  programmaticMove = true;
  map.flyTo([lat, lon], zoom ?? map.getZoom(), {
    animate: animate && !prefersReducedMotion(),
    duration: 0.75,
  });
  setTimeout(() => { programmaticMove = false; }, 900);
}

export function fitCity(city) {
  if (!map || !city) return;
  focus(city.latitude, city.longitude, city.default_zoom || 12);
}

export function fitSightings(list) {
  if (!map || !list || !list.length) return;
  const bounds = L.latLngBounds(list.map((s) => [s.latitude, s.longitude]));
  programmaticMove = true;
  map.fitBounds(bounds, { padding: [60, 60], maxZoom: 15, animate: !prefersReducedMotion() });
  setTimeout(() => { programmaticMove = false; }, 900);
}

export function invalidate() {
  if (map) setTimeout(() => map.invalidateSize(), 60);
}

function prefersReducedMotion() {
  return state.prefs.reducedMotion
    || window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

// ---------------------------------------------------------------------------
// Mode switching
// ---------------------------------------------------------------------------

export function setMode(mode) {
  if (!map) return;
  // HEATMAP / PREDICTION / RADAR are overlays on top of the dark base map;
  // MAP / SATELLITE / TERRAIN swap the base tiles.
  if (BASE_LAYERS[mode]) setBaseLayer(mode);
  else setBaseLayer('map');

  document.getElementById('map-canvas')?.classList.toggle('is-radar', mode === 'radar');
  render();
}

// ---------------------------------------------------------------------------
// Marker construction
// ---------------------------------------------------------------------------

function markerIcon(sighting, isSelected, isNew) {
  const tone = isSelected ? 'selected' : confidenceTone(sighting.confidence, sighting.status);
  const live = sighting.status === 'active' || sighting.status === 'confirmed';
  const classes = [
    'mk',
    `mk--${tone}`,
    live ? 'mk--live' : '',
    isNew ? 'mk--new' : '',
  ].filter(Boolean).join(' ');

  const label = `${esc(sighting.area)} · ${Math.round(sighting.confidence)}%`;

  return L.divIcon({
    className: 'mk-wrap',
    iconSize: [18, 18],
    iconAnchor: [9, 9],
    html:
      `<div class="${classes}">` +
        '<div class="mk__ping"></div>' +
        '<div class="mk__ring"></div>' +
        '<div class="mk__core mk__cross"></div>' +
        `<div class="mk__label">${label}</div>` +
      '</div>',
  });
}

function buildMarker(sighting) {
  const isSelected = state.selectedSightingId === sighting.id;
  const isNew = !seenIds.has(sighting.id);
  seenIds.add(sighting.id);

  const marker = L.marker([sighting.latitude, sighting.longitude], {
    icon: markerIcon(sighting, isSelected, isNew),
    pane: 'marks',
    riseOnHover: true,
    keyboard: true,
    title: `${sighting.ref} — ${sighting.area} — ${Math.round(sighting.confidence)}%`,
    alt: `Sighting ${sighting.ref} at ${sighting.area}`,
  });

  marker.on('click', (event) => {
    L.DomEvent.stopPropagation(event);
    sound.play('blip');
    onSelect(sighting.id);
  });

  marker.on('keypress', (event) => {
    if (event.originalEvent.key === 'Enter') onSelect(sighting.id);
  });

  return marker;
}

// ---------------------------------------------------------------------------
// Clustering — screen-space grid, cheap and stable
// ---------------------------------------------------------------------------

function clusterSightings(list) {
  const buckets = new Map();
  for (const sighting of list) {
    const point = map.latLngToContainerPoint([sighting.latitude, sighting.longitude]);
    const key = `${Math.floor(point.x / CLUSTER_GRID_PX)}:${Math.floor(point.y / CLUSTER_GRID_PX)}`;
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(sighting);
  }
  return [...buckets.values()];
}

function buildCluster(group) {
  const lat = group.reduce((sum, s) => sum + s.latitude, 0) / group.length;
  const lon = group.reduce((sum, s) => sum + s.longitude, 0) / group.length;
  const hot = group.some((s) => s.confidence >= 85);

  const marker = L.marker([lat, lon], {
    pane: 'marks',
    icon: L.divIcon({
      className: 'mk-wrap',
      iconSize: [30, 30],
      iconAnchor: [15, 15],
      html: `<div class="mk-cluster${hot ? ' mk-cluster--hot' : ''}">${group.length}</div>`,
    }),
    title: `${group.length} sightings — click to expand`,
  });

  marker.on('click', (event) => {
    L.DomEvent.stopPropagation(event);
    sound.play('blip');
    map.flyTo([lat, lon], Math.min(18, map.getZoom() + 2), {
      animate: !prefersReducedMotion(),
    });
  });

  return marker;
}

// ---------------------------------------------------------------------------
// Heatmap — drawn to a canvas overlay
// ---------------------------------------------------------------------------

function renderHeat(list) {
  clearHeat();
  if (!list.length) return;

  const size = map.getSize();
  const canvas = document.createElement('canvas');
  canvas.width = size.x;
  canvas.height = size.y;
  const ctx = canvas.getContext('2d');

  // Additive radial blobs weighted by confidence, then colour-mapped through
  // the palette so the heat layer matches the rest of the console.
  ctx.globalCompositeOperation = 'lighter';
  for (const sighting of list) {
    const point = map.latLngToContainerPoint([sighting.latitude, sighting.longitude]);
    const radius = Math.max(22, 12 + map.getZoom() * 3.4);
    const weight = Math.max(0.14, sighting.confidence / 100);
    const gradient = ctx.createRadialGradient(point.x, point.y, 0, point.x, point.y, radius);
    gradient.addColorStop(0, `rgba(255,255,255,${0.55 * weight})`);
    gradient.addColorStop(0.45, `rgba(255,255,255,${0.22 * weight})`);
    gradient.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = gradient;
    ctx.beginPath();
    ctx.arc(point.x, point.y, radius, 0, Math.PI * 2);
    ctx.fill();
  }

  const image = ctx.getImageData(0, 0, canvas.width, canvas.height);
  const px = image.data;
  for (let i = 0; i < px.length; i += 4) {
    const intensity = px[i + 3] / 255;
    if (intensity <= 0.01) continue;
    let r; let g; let b;
    if (intensity < 0.3)      { r = 7;   g = 91;  b = 122; }   // deep blue
    else if (intensity < 0.5) { r = 34;  g = 221; b = 245; }   // cyan
    else if (intensity < 0.7) { r = 66;  g = 232; b = 122; }   // green
    else if (intensity < 0.85){ r = 255; g = 181; b = 46; }    // orange
    else                      { r = 255; g = 48;  b = 56; }    // red
    px[i] = r; px[i + 1] = g; px[i + 2] = b;
    px[i + 3] = Math.min(215, intensity * 320);
  }
  ctx.putImageData(image, 0, 0);

  const bounds = map.getBounds();
  heatLayer = L.imageOverlay(canvas.toDataURL(), bounds, {
    pane: 'heat', opacity: 0.78, interactive: false,
  }).addTo(map);
  heatCanvas = canvas;
}

function clearHeat() {
  if (heatLayer) { map.removeLayer(heatLayer); heatLayer = null; }
  heatCanvas = null;
}

// ---------------------------------------------------------------------------
// Prediction layer
// ---------------------------------------------------------------------------

function renderPrediction(prediction) {
  clearPrediction();
  if (!prediction || !prediction.candidates || !prediction.candidates.length) return;

  const { origin, projection, route, candidates } = prediction;

  // Uncertainty zone around the projected position.
  if (projection) {
    const zone = L.circle([projection.latitude, projection.longitude], {
      pane: 'zone',
      radius: Math.max(180, projection.uncertainty_km * 1000),
      color: 'var(--purple)',
      fillColor: 'var(--purple)',
      fillOpacity: 0.09,
      weight: 1,
      dashArray: '3 5',
      interactive: false,
      className: 'pred-zone',
    }).addTo(map);
    predictionLayers.push(zone);
  }

  // Animated dotted route from the last sighting through the projection.
  if (route && route.length > 1) {
    const path = route.map((p) => [p.latitude, p.longitude]);
    const line = L.polyline(path, {
      pane: 'route',
      color: '#22DDF5',
      weight: 2,
      opacity: 0.9,
      dashArray: '6 6',
      className: 'pred-path',
      interactive: false,
    }).addTo(map);
    predictionLayers.push(line);

    // Under-glow so the path reads on busy satellite imagery.
    const glow = L.polyline(path, {
      pane: 'zone', color: '#7137FF', weight: 7, opacity: 0.22, interactive: false,
    }).addTo(map);
    predictionLayers.push(glow);
  }

  // Origin marker.
  if (origin) {
    const marker = L.marker([origin.latitude, origin.longitude], {
      pane: 'marks',
      icon: L.divIcon({
        className: 'mk-wrap',
        iconSize: [18, 18],
        iconAnchor: [9, 9],
        html: '<div class="mk mk--selected mk--live">'
            + '<div class="mk__ring"></div><div class="mk__core mk__cross"></div>'
            + '<div class="mk__label">LAST KNOWN</div></div>',
      }),
      title: `Last known position — ${origin.area}`,
    }).addTo(map);
    predictionLayers.push(marker);
  }

  // Candidate destinations, sized by probability.
  candidates.slice(0, 5).forEach((candidate, index) => {
    const marker = L.marker([candidate.latitude, candidate.longitude], {
      pane: 'marks',
      icon: L.divIcon({
        className: 'mk-wrap',
        iconSize: [18, 18],
        iconAnchor: [9, 9],
        html: '<div class="mk mk--predicted">'
            + '<div class="mk__ring"></div><div class="mk__core"></div>'
            + `<div class="mk__label">${esc(candidate.name)} ${Math.round(candidate.probability)}%</div>`
            + '</div>',
      }),
      opacity: index === 0 ? 1 : 0.55 + (candidate.probability / 250),
      title: `${candidate.name} — ${candidate.probability}% predicted`,
    }).addTo(map);
    predictionLayers.push(marker);

    const ring = L.circle([candidate.latitude, candidate.longitude], {
      pane: 'zone',
      radius: Math.max(220, candidate.probability * 26),
      color: '#7137FF',
      fillColor: '#7137FF',
      fillOpacity: 0.06 + candidate.probability / 1400,
      weight: 1,
      opacity: 0.4,
      interactive: false,
    }).addTo(map);
    predictionLayers.push(ring);
  });
}

function clearPrediction() {
  predictionLayers.forEach((layer) => map.removeLayer(layer));
  predictionLayers = [];
}

// ---------------------------------------------------------------------------
// Cameras
// ---------------------------------------------------------------------------

function renderCameras(cameras, show) {
  for (const [, marker] of cameraMarkers) map.removeLayer(marker);
  cameraMarkers.clear();
  if (!show) return;

  for (const camera of cameras) {
    if (camera.city_id !== state.cityId) continue;
    const marker = L.marker([camera.latitude, camera.longitude], {
      pane: 'cameras',
      icon: L.divIcon({
        className: 'mk-wrap',
        iconSize: [14, 14],
        iconAnchor: [7, 7],
        html: `<div class="mk mk--camera is-${camera.status}">`
            + '<div class="mk__core"></div>'
            + `<div class="mk__label">${esc(camera.id)} ${camera.status.toUpperCase()}</div>`
            + '</div>',
      }),
      title: `${camera.id} — ${camera.label} (${camera.status})`,
    });
    marker.on('click', (event) => {
      L.DomEvent.stopPropagation(event);
      set({ selectedCameraId: camera.id });
      window.dispatchEvent(new CustomEvent('spidey:camera-open', { detail: camera.id }));
    });
    marker.addTo(map);
    cameraMarkers.set(camera.id, marker);
  }
}

// ---------------------------------------------------------------------------
// Main render
// ---------------------------------------------------------------------------

let renderQueued = false;

export function render() {
  if (!map) return;
  if (renderQueued) return;
  renderQueued = true;
  requestAnimationFrame(() => {
    renderQueued = false;
    doRender();
  });
}

function doRender() {
  const mode = state.mapMode;
  const all = visibleSightings();

  // Viewport filtering: only pay for markers that could be seen. Padded so
  // panning does not reveal empty space before the next render.
  const bounds = map.getBounds().pad(0.35);
  let inView = all.filter((s) => bounds.contains([s.latitude, s.longitude]));
  if (inView.length > MAX_MARKERS) {
    inView = inView
      .slice()
      .sort((a, b) => b.confidence - a.confidence || b.ts - a.ts)
      .slice(0, MAX_MARKERS);
  }

  // --- heat
  if (mode === 'heatmap') renderHeat(all);
  else clearHeat();

  // --- prediction
  if (mode === 'prediction' || mode === 'radar') renderPrediction(state.prediction);
  else if (state.prediction && state.selectedSightingId === null && mode === 'map') {
    renderPrediction(state.prediction);   // keep the path visible on the default view
  } else clearPrediction();

  // --- cameras
  renderCameras(state.cameras, mode !== 'heatmap');

  // --- sighting markers
  clusterLayers.forEach((layer) => map.removeLayer(layer));
  clusterLayers = [];

  const shouldCluster = inView.length > CLUSTER_THRESHOLD && map.getZoom() < 15;

  if (mode === 'heatmap') {
    // Heat mode shows density, not individual records; keep only the selection.
    for (const [, marker] of markerIndex) map.removeLayer(marker);
    markerIndex.clear();
    const selected = selectedSighting();
    if (selected) {
      const marker = buildMarker(selected);
      marker.addTo(map);
      markerIndex.set(selected.id, marker);
    }
    updateReadout(all, inView);
    return;
  }

  if (shouldCluster) {
    for (const [, marker] of markerIndex) map.removeLayer(marker);
    markerIndex.clear();

    for (const group of clusterSightings(inView)) {
      if (group.length === 1) {
        const marker = buildMarker(group[0]);
        marker.addTo(map);
        markerIndex.set(group[0].id, marker);
      } else {
        const cluster = buildCluster(group);
        cluster.addTo(map);
        clusterLayers.push(cluster);
      }
    }
  } else {
    const wanted = new Set(inView.map((s) => s.id));

    for (const [id, marker] of markerIndex) {
      if (!wanted.has(id)) {
        map.removeLayer(marker);
        markerIndex.delete(id);
      }
    }

    for (const sighting of inView) {
      const existing = markerIndex.get(sighting.id);
      if (existing) {
        // Refresh icon only when the visual state actually changed.
        const tone = state.selectedSightingId === sighting.id
          ? 'selected'
          : confidenceTone(sighting.confidence, sighting.status);
        if (existing._tone !== tone) {
          existing.setIcon(markerIcon(sighting, tone === 'selected', false));
          existing._tone = tone;
        }
        existing.setLatLng([sighting.latitude, sighting.longitude]);
      } else {
        const marker = buildMarker(sighting);
        marker._tone = state.selectedSightingId === sighting.id
          ? 'selected'
          : confidenceTone(sighting.confidence, sighting.status);
        marker.addTo(map);
        markerIndex.set(sighting.id, marker);
      }
    }
  }

  updateReadout(all, inView);
}

function updateReadout(all, inView) {
  const node = document.getElementById('map-readout');
  if (!node) return;
  const center = map.getCenter();
  const latest = all.length ? all.reduce((a, b) => (b.ts > a.ts ? b : a)) : null;

  node.innerHTML =
    `<div>MODE <b>${state.mapMode.toUpperCase()}</b></div>` +
    `<div>PLOTTED <b>${inView.length}</b> / ${all.length}</div>` +
    `<div>ZOOM <b>${map.getZoom().toFixed(1)}</b></div>` +
    `<div>LAT <b>${center.lat.toFixed(4)}</b></div>` +
    `<div>LON <b>${center.lng.toFixed(4)}</b></div>` +
    (latest ? `<div>LAST <b>${esc(agoFromTs(latest.ts))}</b></div>` : '');
}

/** Flash a marker when a brand-new sighting arrives. */
export function pulseNew(sighting) {
  seenIds.delete(sighting.id);
  render();
}

export function clearSeen() {
  seenIds = new Set();
}
