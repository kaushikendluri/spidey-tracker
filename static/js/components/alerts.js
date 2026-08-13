/**
 * Alert toasts.
 *
 * Only genuinely notable events raise one — a high-confidence sighting, a
 * camera detection, an operation failing. Each carries a working VIEW action,
 * auto-dismisses on a visible countdown, and pauses that countdown on hover so
 * it cannot vanish while being read.
 */

import { state } from '../core/store.js';
import { el, esc, agoFromTs, ago } from '../core/format.js';
import * as sound from '../core/sound.js';

const MAX_VISIBLE = 3;
const LIFETIME_MS = 9000;

let container = null;
const live = new Set();

export function initAlerts() {
  container = document.getElementById('alerts');
}

export function push({ title, body, severity = 'critical', sightingId = null,
                       confidence = null, area = null, ts = null, isDemo = false }) {
  if (!container) return null;

  // Oldest first out when the stack is full, so the newest is always visible.
  while (live.size >= MAX_VISIBLE) {
    const oldest = live.values().next().value;
    dismiss(oldest);
  }

  const node = el('div.alert', {
    class: `alert--${severity}`,
    role: 'alert',
  }, [
    el('div.alert__head', {}, [
      el('span.dot.dot--pulse'),
      el('span', { text: title }),
      isDemo ? el('span.badge.badge--demo', { style: { marginLeft: 'auto' } }, ['DEMO']) : null,
    ]),

    area ? el('div.alert__area', { text: area }) : null,
    body ? el('div.alert__row', {}, [el('span', { text: body })]) : null,

    confidence !== null
      ? el('div.alert__row', {}, [
          el('span', { text: 'CONFIDENCE' }),
          el('b', { text: `${Math.round(confidence)}%` }),
        ])
      : null,

    ts
      ? el('div.alert__row', {}, [
          el('span', { text: 'DETECTED' }),
          el('b', { text: agoFromTs(ts) }),
        ])
      : null,

    el('div', { style: { display: 'flex', gap: 'var(--sp-2)', marginTop: 'var(--sp-2)' } }, [
      sightingId
        ? el('button.btn.btn--sm.btn--block', {
            type: 'button',
            onclick: () => {
              window.dispatchEvent(new CustomEvent('spidey:select', { detail: sightingId }));
              dismiss(node);
            },
          }, ['VIEW SIGHTING'])
        : null,
      el('button.btn.btn--sm.btn--ghost', {
        type: 'button',
        'aria-label': 'Dismiss alert',
        onclick: () => dismiss(node),
      }, ['×']),
    ]),

    el('div.alert__timer'),
  ]);

  // Pause the auto-dismiss while the pointer is over the toast.
  const timer = node.querySelector('.alert__timer');
  let timeout = setTimeout(() => dismiss(node), LIFETIME_MS);

  node.addEventListener('mouseenter', () => {
    clearTimeout(timeout);
    if (timer) timer.style.animationPlayState = 'paused';
  });
  node.addEventListener('mouseleave', () => {
    if (timer) timer.style.animationPlayState = 'running';
    timeout = setTimeout(() => dismiss(node), 2500);
  });
  node._timeout = () => clearTimeout(timeout);

  container.appendChild(node);
  live.add(node);

  sound.play(severity === 'critical' ? 'alert' : 'ping');
  return node;
}

export function dismiss(node) {
  if (!node || !live.has(node)) return;
  live.delete(node);
  if (node._timeout) node._timeout();
  node.classList.add('is-leaving');
  setTimeout(() => node.remove(), 220);
}

export function dismissAll() {
  for (const node of [...live]) dismiss(node);
}

/** Build a toast from a server `alert.created` payload. */
export function fromEvent(payload) {
  const sighting = payload.sighting || {};
  return push({
    title: payload.title || 'ALERT',
    severity: payload.severity === 'critical' ? 'critical'
            : payload.severity === 'warning' ? 'warning' : 'info',
    area: sighting.area || null,
    confidence: sighting.confidence ?? null,
    ts: sighting.ts || payload.created_at,
    sightingId: payload.sighting_id || sighting.id || null,
    isDemo: Boolean(payload.is_demo),
  });
}
