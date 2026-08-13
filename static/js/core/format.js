/**
 * Formatting helpers and safe DOM construction.
 *
 * `el()` and `esc()` exist so that no user-supplied string (descriptions,
 * reporter names, search terms) is ever concatenated into innerHTML.
 */

export function esc(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** Create an element: el('div.panel', {aria-label:'x'}, [children]) */
export function el(spec, attrs = {}, children = []) {
  const [tagAndId, ...classes] = String(spec).split('.');
  const [tag, id] = tagAndId.split('#');
  const node = document.createElement(tag || 'div');
  if (id) node.id = id;
  if (classes.length) node.className = classes.join(' ');

  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = `${node.className} ${value}`.trim();
    else if (key === 'text') node.textContent = value;
    else if (key === 'html') node.innerHTML = value;      // callers pass built markup only
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key === 'style' && typeof value === 'object') Object.assign(node.style, value);
    else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else node.setAttribute(key, value === true ? '' : value);
  }

  const list = Array.isArray(children) ? children : [children];
  for (const child of list) {
    if (child === null || child === undefined || child === false) continue;
    node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
  }
  return node;
}

export function clear(node) {
  while (node && node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export function mount(node, children) {
  clear(node);
  const list = Array.isArray(children) ? children : [children];
  for (const child of list) if (child) node.appendChild(child);
  return node;
}

// --- time -----------------------------------------------------------------

export function ago(seconds) {
  if (seconds === null || seconds === undefined) return '—';
  const s = Math.max(0, seconds);
  if (s < 10) return 'JUST NOW';
  if (s < 60) return `${Math.floor(s)} SEC AGO`;
  const m = s / 60;
  if (m < 60) return `${Math.floor(m)} MIN AGO`;
  const h = m / 60;
  if (h < 24) return `${Math.floor(h)} HR AGO`;
  return `${Math.floor(h / 24)} D AGO`;
}

export function agoFromTs(ts) {
  if (!ts) return '—';
  return ago(Date.now() / 1000 - ts);
}

export function clockTime(ts) {
  const d = ts ? new Date(ts * 1000) : new Date();
  return d.toLocaleTimeString([], { hour12: false });
}

export function shortTime(ts) {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: '2-digit', minute: '2-digit', hour12: false,
  });
}

export function dateTime(ts) {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleString([], {
    month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  });
}

export function duration(minutes) {
  if (minutes === null || minutes === undefined) return '—';
  if (minutes < 60) return `${Math.round(minutes)} MIN`;
  return `${(minutes / 60).toFixed(1)} HR`;
}

// --- numbers --------------------------------------------------------------

export const num = (value, digits = 0) =>
  value === null || value === undefined || Number.isNaN(value)
    ? '—'
    : Number(value).toLocaleString(undefined, {
        minimumFractionDigits: digits, maximumFractionDigits: digits,
      });

export const pct = (value, digits = 0) =>
  value === null || value === undefined ? '—' : `${Number(value).toFixed(digits)}%`;

export const km = (value) =>
  value === null || value === undefined ? '—' : `${Number(value).toFixed(1)} KM`;

export const kmh = (value) =>
  value === null || value === undefined ? '—' : `${Math.round(value)} KM/H`;

// --- domain vocabulary ----------------------------------------------------

/**
 * Confidence -> visual tone. Single source of truth: the map markers, the feed,
 * the bars and the detail panel all colour from this, so a 94% sighting is the
 * same green everywhere.
 */
export function confidenceTone(confidence, status) {
  if (status === 'expired' || status === 'dismissed') return 'expired';
  if (confidence >= 85) return 'confirmed';
  if (status === 'active' && confidence >= 70) return 'active';
  if (confidence >= 60) return 'medium';
  return 'unverified';
}

export const TONE_COLOR = {
  confirmed: 'var(--green)',
  active: 'var(--red)',
  medium: 'var(--orange)',
  unverified: 'var(--text)',
  expired: 'var(--text-dim)',
  selected: 'var(--cyan-bright)',
  predicted: 'var(--purple-bright)',
};

export const TONE_CLASS = {
  confirmed: 't-green',
  active: 't-red',
  medium: 't-orange',
  unverified: 't-white',
  expired: 'dim',
};

export const BAR_CLASS = {
  confirmed: 'bar__fill--green',
  active: 'bar__fill--red',
  medium: 'bar__fill--orange',
  unverified: 'bar__fill--white',
  expired: 'bar__fill--white',
};

export const SOURCE_LABEL = {
  citizen: 'CITIZEN REPORT',
  camera: 'CAMERA NET',
  network: 'GLOBAL NET',
  demo: 'DEMO NET',
};

export const STATUS_LABEL = {
  active: 'ACTIVE',
  confirmed: 'CONFIRMED',
  unverified: 'UNVERIFIED',
  expired: 'EXPIRED',
  dismissed: 'DISMISSED',
};

export const CAMERA_TONE = {
  live: 'green',
  detected: 'red',
  analyzing: 'orange',
  offline: 'muted',
  error: 'red',
};

// --- misc -----------------------------------------------------------------

export function debounce(fn, wait = 220) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

export function throttle(fn, wait = 200) {
  let last = 0;
  let timer = null;
  return (...args) => {
    const now = Date.now();
    const remaining = wait - (now - last);
    if (remaining <= 0) {
      last = now;
      fn(...args);
    } else if (!timer) {
      timer = setTimeout(() => {
        last = Date.now();
        timer = null;
        fn(...args);
      }, remaining);
    }
  };
}

/** Pixel-styled bar row used across the analysis, prediction and stats panels. */
export function barRow(label, value, toneClass = '', suffix = '%') {
  const track = el('div.bar__track', {}, [
    el('div.bar__fill', { class: toneClass, style: { width: '0%' } }),
  ]);
  const row = el('div.bar', {}, [
    el('div.bar__label', { text: label }),
    track,
    el('div.bar__value', { text: value === null || value === undefined ? '—' : `${Math.round(value)}${suffix}` }),
  ]);
  // Animate on the next frame so the transition actually runs.
  requestAnimationFrame(() => {
    track.firstChild.style.width = `${Math.max(0, Math.min(100, value || 0))}%`;
  });
  return row;
}

export function kv(key, value, valueClass = '') {
  return el('div.kv', {}, [
    el('div.kv__k', { text: key }),
    el('div.kv__v', { class: valueClass, text: value === null || value === undefined ? '—' : String(value) }),
  ]);
}

export function statTile(label, value, valueClass = '', sub = '') {
  return el('div.stat', {}, [
    el('div.stat__label', { text: label }),
    el('div.stat__value', { class: valueClass, text: value }),
    sub ? el('div.stat__sub', { text: sub }) : null,
  ]);
}

export function emptyState(...lines) {
  return el('div.empty', {}, lines.map((line) => el('div', { text: line })));
}

export function demoBadge() {
  return el('span.badge.badge--demo', { title: 'Simulated record from the demo network' }, ['DEMO']);
}
