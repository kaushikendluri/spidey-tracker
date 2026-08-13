/**
 * Dashboard panels: status, live feed, local area, prediction, radar,
 * camera grid, analytics, system status and the global network ticker.
 *
 * Every renderer reads from the store and is re-invoked when the keys it
 * depends on change. None of them hold their own copy of domain data, and none
 * contain a hardcoded figure.
 */

import {
  state, visibleSightings, latestSighting, selectedSighting, activeCity, hasActiveFilters,
} from '../core/store.js';
import {
  el, mount, clear, esc, ago, agoFromTs, shortTime, dateTime, num, pct, km, kmh,
  confidenceTone, TONE_CLASS, BAR_CLASS, SOURCE_LABEL, STATUS_LABEL, CAMERA_TONE,
  barRow, kv, statTile, emptyState, demoBadge, duration,
} from '../core/format.js';

// ---------------------------------------------------------------------------
// CURRENT STATUS
// ---------------------------------------------------------------------------

export function renderStatus() {
  const body = document.getElementById('status-body');
  const meta = document.getElementById('status-meta');
  if (!body) return;

  const latest = latestSighting();
  const city = activeCity();

  if (meta) {
    clear(meta);
    meta.appendChild(el('span', { text: city ? city.name : '—' }));
    if (state.demoMode) meta.appendChild(demoBadge());
  }

  if (!latest) {
    mount(body, emptyState(
      'NO SIGHTINGS MATCH',
      hasActiveFilters() ? 'CLEAR FILTERS TO SEE MORE' : 'AWAITING NETWORK ACTIVITY',
    ));
    return;
  }

  const tone = confidenceTone(latest.confidence, latest.status);

  mount(body, [
    el('div', { style: { marginBottom: 'var(--sp-4)' } }, [
      el('div.px-xs.dim', { text: 'LAST SIGHTING' }),
      el('div.term-xl.t-cyan', { text: agoFromTs(latest.ts) }),
      el('div.px-sm', {
        style: { color: 'var(--text)', marginTop: 'var(--sp-2)' },
        text: latest.area,
      }),
    ]),

    el('div', {
      style: { display: 'flex', alignItems: 'flex-end', gap: 'var(--sp-5)',
               marginBottom: 'var(--sp-4)' },
    }, [
      el('div', {}, [
        el('div.px-xs.dim', { text: 'CONFIDENCE' }),
        el('div.term-2xl', { class: TONE_CLASS[tone], text: `${Math.round(latest.confidence)}%` }),
      ]),
      el('div', { style: { flex: '1 1 auto' } }, [
        el('div.px-xs.dim', { text: 'STATUS' }),
        el('div.px-sm', {
          class: TONE_CLASS[tone],
          text: STATUS_LABEL[latest.status] || latest.status.toUpperCase(),
        }),
      ]),
      latest.is_demo ? demoBadge() : null,
    ]),

    kv('DIRECTION', latest.direction_label
      ? `${latest.direction_label} ${Math.round(latest.direction)}°` : '—'),
    kv('EST SPEED', kmh(latest.speed_kmh)),
    kv('SOURCE', latest.camera_id
      ? `${latest.camera_id}` : (SOURCE_LABEL[latest.source] || latest.source.toUpperCase())),
    kv('OBSERVED', shortTime(latest.ts)),
    kv('REF', latest.ref),

    el('div', { style: { marginTop: 'var(--sp-4)', display: 'flex', gap: 'var(--sp-2)' } }, [
      el('button.btn.btn--sm.btn--block', {
        type: 'button',
        onclick: () => window.dispatchEvent(
          new CustomEvent('spidey:select', { detail: latest.id }),
        ),
      }, ['OPEN DOSSIER']),
    ]),
  ]);
}

// ---------------------------------------------------------------------------
// LIVE FEED
// ---------------------------------------------------------------------------

export function feedItem(sighting, { animate = false } = {}) {
  const tone = confidenceTone(sighting.confidence, sighting.status);
  const selected = state.selectedSightingId === sighting.id;

  const thumb = el('div.feed__thumb', {}, [
    sighting.image_url
      ? el('img', { src: sighting.image_url, alt: '', loading: 'lazy' })
      : el('div.feed__thumb-glyph', { text: sighting.source === 'camera' ? 'CAM' : 'RPT' }),
  ]);

  return el('button.feed__item', {
    class: `${selected ? 'is-selected' : ''} ${animate ? 'feed__item--enter' : ''}`.trim(),
    type: 'button',
    dataset: { id: sighting.id },
    'aria-label': `${sighting.ref}, ${sighting.area}, ${Math.round(sighting.confidence)} percent confidence`,
    onclick: () => window.dispatchEvent(new CustomEvent('spidey:select', { detail: sighting.id })),
  }, [
    thumb,
    el('div.feed__main', {}, [
      el('div.feed__area', { text: sighting.area }),
      el('div.feed__sub', {}, [
        el('span', { text: agoFromTs(sighting.ts) }),
        el('span.dim', { text: '·' }),
        el('span', { text: SOURCE_LABEL[sighting.source] || sighting.source }),
        sighting.is_demo ? el('span.badge.badge--demo', { style: { fontSize: '5px' } }, ['DEMO']) : null,
      ]),
    ]),
    el('div.feed__conf', { class: TONE_CLASS[tone], text: `${Math.round(sighting.confidence)}%` }),
  ]);
}

export function renderFeed(limit = 40, newIds = new Set()) {
  const body = document.getElementById('feed-body');
  const count = document.getElementById('feed-count');
  if (!body) return;

  const list = visibleSightings();
  if (count) count.textContent = String(list.length);

  const navCount = document.getElementById('nav-feed-count');
  if (navCount) {
    const recent = list.filter((s) => Date.now() / 1000 - s.ts < 300).length;
    navCount.textContent = String(recent);
    navCount.hidden = recent === 0;
  }

  if (!list.length) {
    mount(body, emptyState(
      'NO ACTIVITY',
      hasActiveFilters() ? 'FILTERS ARE HIDING RESULTS' : 'THE FEED WILL POPULATE LIVE',
    ));
    return;
  }

  mount(body, el('div.feed', {}, list.slice(0, limit).map(
    (s) => feedItem(s, { animate: newIds.has(s.id) }),
  )));
}

// ---------------------------------------------------------------------------
// LOCAL AREA
// ---------------------------------------------------------------------------

export function renderArea() {
  const body = document.getElementById('area-body');
  const meta = document.getElementById('area-meta');
  if (!body) return;

  const list = visibleSightings();
  const byArea = new Map();
  for (const sighting of list) {
    const entry = byArea.get(sighting.area) || { count: 0, conf: 0, last: 0, high: 0 };
    entry.count += 1;
    entry.conf += sighting.confidence;
    entry.high += sighting.confidence >= 85 ? 1 : 0;
    entry.last = Math.max(entry.last, sighting.ts);
    byArea.set(sighting.area, entry);
  }

  const rows = [...byArea.entries()]
    .map(([area, e]) => ({ area, ...e, avg: e.conf / e.count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 7);

  if (meta) meta.textContent = `${byArea.size} AREAS`;

  if (!rows.length) {
    mount(body, emptyState('NO LOCAL ACTIVITY'));
    return;
  }

  const peak = Math.max(...rows.map((r) => r.count));

  mount(body, el('div.hbar', {}, rows.map((row) => {
    const tone = row.avg >= 85 ? 'confirmed' : row.avg >= 60 ? 'medium' : 'unverified';
    const track = el('div.bar__track', {}, [
      el('div.bar__fill', { class: BAR_CLASS[tone], style: { width: '0%' } }),
    ]);
    requestAnimationFrame(() => {
      track.firstChild.style.width = `${(row.count / peak) * 100}%`;
    });
    return el('button.hbar__row', {
      type: 'button',
      title: `${row.area} — ${row.count} sightings, ${Math.round(row.avg)}% avg, last ${agoFromTs(row.last)}`,
      onclick: () => window.dispatchEvent(
        new CustomEvent('spidey:focus-area', { detail: row.area }),
      ),
    }, [
      el('div.hbar__name', { text: row.area }),
      track,
      el('div.bar__value', { text: String(row.count) }),
    ]);
  })));
}

// ---------------------------------------------------------------------------
// PREDICTION
// ---------------------------------------------------------------------------

export function renderPrediction() {
  const body = document.getElementById('pred-body');
  const meta = document.getElementById('pred-meta');
  if (!body) return;

  const prediction = state.prediction;

  if (meta) {
    clear(meta);
    if (prediction && !prediction.sparse) {
      meta.appendChild(el('span', { text: `${prediction.samples} SAMPLES` }));
    }
    meta.appendChild(el('button.btn.btn--sm.btn--ghost', {
      type: 'button',
      title: 'Recompute the prediction now',
      onclick: () => window.dispatchEvent(new CustomEvent('spidey:refresh-prediction')),
    }, ['↻']));
  }

  if (!prediction || !prediction.candidates || !prediction.candidates.length) {
    mount(body, emptyState(
      'INSUFFICIENT MOVEMENT DATA',
      prediction && prediction.reason ? prediction.reason.toUpperCase() : 'AWAITING SIGHTINGS',
    ));
    return;
  }

  const lead = prediction.candidates[0];
  const vector = prediction.vector;

  mount(body, [
    el('div.pred__lead', {}, [
      el('div', {}, [
        el('div.px-xs.dim', { text: 'MOST LIKELY' }),
        el('div.pred__lead-name', { text: lead.name }),
      ]),
      el('div.pred__lead-prob', { text: `${Math.round(lead.probability)}%` }),
    ]),

    ...prediction.candidates.slice(0, 5).map((candidate) => {
      const track = el('div.bar__track', {}, [
        el('div.bar__fill.bar__fill--purple', { style: { width: '0%' } }),
      ]);
      requestAnimationFrame(() => {
        track.firstChild.style.width = `${candidate.probability}%`;
      });
      return el('button.pred__row', {
        type: 'button',
        title: `${candidate.name} — ${candidate.distance_km} km away, ETA ${candidate.eta_min} min`,
        onclick: () => window.dispatchEvent(
          new CustomEvent('spidey:focus-point', {
            detail: { lat: candidate.latitude, lon: candidate.longitude, zoom: 14 },
          }),
        ),
      }, [
        el('div.pred__row-name', { text: candidate.name }),
        el('div.bar__value', { text: `${Math.round(candidate.probability)}%` }),
        el('div.pred__row-bar', {}, [track]),
      ]);
    }),

    el('div.pred__eta', {}, [
      el('div', {}, [
        el('div.px-xs.dim', { text: 'ETA WINDOW' }),
        el('div.term-lg.t-cyan', {
          text: prediction.eta_min !== null
            ? `${Math.round(prediction.eta_min)}–${Math.round(prediction.eta_max)} MIN`
            : '—',
        }),
      ]),
      el('div', { style: { textAlign: 'right' } }, [
        el('div.px-xs.dim', { text: 'CONFIDENCE' }),
        el('div.term-lg.t-purple', { text: `${Math.round(prediction.confidence)}%` }),
      ]),
    ]),

    vector
      ? el('div', { style: { marginTop: 'var(--sp-3)' } }, [
          kv('VECTOR', `${vector.compass} ${Math.round(vector.heading)}°`),
          kv('SPEED', kmh(vector.speed_kmh)),
          kv('COHERENCE', pct(vector.coherence * 100)),
          kv('LEGS USED', String(vector.legs)),
        ])
      : el('div.px-xs.dim', {
          style: { marginTop: 'var(--sp-3)' },
          text: 'NO COHERENT MOVEMENT VECTOR',
        }),

    el('div.px-xs.dim', {
      style: { marginTop: 'var(--sp-3)', lineHeight: '1.8' },
      text: `METHOD: ${(prediction.method || '').toUpperCase()}`,
    }),
  ]);
}

// ---------------------------------------------------------------------------
// RADAR
// ---------------------------------------------------------------------------

export function renderRadar(container) {
  const list = visibleSightings();
  const city = activeCity();
  const selected = selectedSighting();

  // Centre the radar on the selection when there is one, else the city.
  const center = selected
    ? { lat: selected.latitude, lon: selected.longitude }
    : city ? { lat: city.latitude, lon: city.longitude } : null;

  if (!center) { mount(container, emptyState('NO REFERENCE POINT')); return; }

  const RANGE_KM = 12;
  const radar = el('div.radar', { role: 'img', 'aria-label': `Radar showing ${list.length} sightings within ${RANGE_KM} km` });

  const grid = el('div.radar__grid');
  [25, 50, 75, 100].forEach((size) => {
    grid.appendChild(el('div.radar__ring', {
      style: { width: `${size}%`, height: `${size}%` },
    }));
  });
  grid.appendChild(el('div.radar__cross.radar__cross--h'));
  grid.appendChild(el('div.radar__cross.radar__cross--v'));
  radar.appendChild(grid);
  radar.appendChild(el('div.radar__sweep'));
  radar.appendChild(el('div.radar__center'));

  // Project each sighting into radar space using a local flat approximation —
  // accurate enough over a 12 km radius and far cheaper than full projection.
  let plotted = 0;
  for (const sighting of list) {
    const dLat = sighting.latitude - center.lat;
    const dLon = (sighting.longitude - center.lon)
      * Math.cos((center.lat * Math.PI) / 180);
    const northKm = dLat * 111.32;
    const eastKm = dLon * 111.32;
    const distance = Math.hypot(northKm, eastKm);
    if (distance > RANGE_KM) continue;

    const x = 50 + (eastKm / RANGE_KM) * 50;
    const y = 50 - (northKm / RANGE_KM) * 50;
    const isSelected = selected && selected.id === sighting.id;

    radar.appendChild(el('div.radar__blip', {
      class: isSelected ? 'radar__blip--selected'
           : sighting.confidence >= 85 ? '' : 'radar__blip--hot',
      style: { left: `${x}%`, top: `${y}%` },
      title: `${sighting.ref} — ${sighting.area} — ${distance.toFixed(1)} km`,
    }));
    plotted += 1;
  }

  // Prediction candidates appear as distinct purple blips.
  if (state.prediction && state.prediction.candidates) {
    for (const candidate of state.prediction.candidates.slice(0, 3)) {
      const dLat = candidate.latitude - center.lat;
      const dLon = (candidate.longitude - center.lon)
        * Math.cos((center.lat * Math.PI) / 180);
      const northKm = dLat * 111.32;
      const eastKm = dLon * 111.32;
      if (Math.hypot(northKm, eastKm) > RANGE_KM) continue;
      radar.appendChild(el('div.radar__blip.radar__blip--pred', {
        style: {
          left: `${50 + (eastKm / RANGE_KM) * 50}%`,
          top: `${50 - (northKm / RANGE_KM) * 50}%`,
        },
        title: `PREDICTED — ${candidate.name} ${candidate.probability}%`,
      }));
    }
  }

  radar.appendChild(el('div.radar__scale', { text: `${RANGE_KM} KM` }));

  mount(container, [
    radar,
    el('div', { style: { marginTop: 'var(--sp-4)' } }, [
      kv('CENTRE', selected ? selected.area : (city ? city.name : '—')),
      kv('CONTACTS', String(plotted)),
      kv('RANGE', `${RANGE_KM} KM`),
      kv('SWEEP', state.prefs.reducedMotion ? 'STATIC' : 'ACTIVE'),
    ]),
  ]);
}

// ---------------------------------------------------------------------------
// CAMERA GRID
// ---------------------------------------------------------------------------

export function cameraTile(camera) {
  const tone = CAMERA_TONE[camera.status] || 'muted';
  const detection = camera.last_detection;
  const justDetected = camera.status === 'detected'
    || (detection && detection.age_sec !== undefined && detection.age_sec < 60);

  return el('button.cam', {
    class: `is-${camera.status}`,
    type: 'button',
    dataset: { camera: camera.id },
    'aria-label': `Camera ${camera.id}, ${camera.label}, ${camera.status}`,
    onclick: () => window.dispatchEvent(
      new CustomEvent('spidey:camera-open', { detail: camera.id }),
    ),
  }, [
    el('div.cam__view', {}, [
      el('div.cam__noise'),
      el('div.cam__sweep'),
      el('div.cam__id', { text: camera.id }),
      el('div.cam__state', {}, [
        el('span.badge', { class: `badge--${tone}` }, [
          el('span.dot', { class: camera.status === 'live' ? 'dot--pulse' : '' }),
          camera.status.toUpperCase(),
        ]),
      ]),
      justDetected ? el('div.cam__reticle') : null,
      el('div.cam__mock', { text: camera.is_mock ? 'SIMULATED FEED' : 'FEED' }),
    ]),
    el('div.cam__foot', {}, [
      el('div.cam__label', { text: camera.label }),
      el('div.cam__det', {
        text: detection
          ? `${Math.round(detection.confidence)}% · ${ago(detection.age_sec)}`
          : 'NO DETECTIONS',
      }),
    ]),
  ]);
}

export function renderCameras(container, limit = 8) {
  const cameras = state.cameras.filter((c) => c.city_id === state.cityId);

  if (!cameras.length) {
    mount(container, emptyState('NO CAMERAS ON THIS GRID'));
    return;
  }

  // Most interesting first: detecting, then analysing, then recent detections.
  const priority = { detected: 0, analyzing: 1, live: 2, error: 3, offline: 4 };
  const sorted = cameras.slice().sort((a, b) => {
    const p = (priority[a.status] ?? 9) - (priority[b.status] ?? 9);
    if (p !== 0) return p;
    const at = a.last_detection ? a.last_detection.ts : 0;
    const bt = b.last_detection ? b.last_detection.ts : 0;
    return bt - at;
  });

  const counts = {};
  for (const camera of cameras) counts[camera.status] = (counts[camera.status] || 0) + 1;

  mount(container, [
    el('div', {
      style: { display: 'flex', gap: 'var(--sp-2)', flexWrap: 'wrap',
               marginBottom: 'var(--sp-3)', alignItems: 'center' },
    }, [
      ...Object.entries(counts).map(([status, count]) =>
        el('span.badge', { class: `badge--${CAMERA_TONE[status] || 'muted'}` },
          [`${count} ${status.toUpperCase()}`])),
      el('span.px-xs.dim', {
        style: { marginLeft: 'auto' },
        text: 'MOCK FEEDS — NO REAL CCTV ACCESS',
      }),
    ]),
    el('div.camgrid', {}, sorted.slice(0, limit).map(cameraTile)),
  ]);
}

// ---------------------------------------------------------------------------
// ANALYTICS
// ---------------------------------------------------------------------------

export function renderAnalytics(container, { full = false } = {}) {
  const data = state.analytics;
  if (!data) { mount(container, emptyState('LOADING ANALYTICS…')); return; }

  const { summary, timeline, areas, confidence, sources, hours, cameras } = data;

  const ranges = ['1h', '24h', '7d', '30d'];
  const rangeBar = el('div', {
    style: { display: 'flex', gap: 'var(--sp-2)', marginBottom: 'var(--sp-4)',
             alignItems: 'center', flexWrap: 'wrap' },
  }, [
    ...ranges.map((range) => el('button.btn.btn--sm', {
      class: state.analyticsRange === range ? 'btn--active' : '',
      type: 'button',
      onclick: () => window.dispatchEvent(
        new CustomEvent('spidey:set-range', { detail: range }),
      ),
    }, [range.toUpperCase()])),
    summary.demo_records > 0
      ? el('span.badge.badge--demo', {
          style: { marginLeft: 'auto' },
          title: 'Some or all records in this window are simulated',
        }, [`${summary.demo_records} DEMO OF ${summary.total}`])
      : null,
  ]);

  const tiles = el('div', {
    style: { display: 'grid', gap: 'var(--sp-2)', marginBottom: 'var(--sp-4)',
             gridTemplateColumns: 'repeat(auto-fit, minmax(88px, 1fr))' },
  }, [
    statTile('TOTAL', num(summary.total)),
    statTile('HIGH CONF', num(summary.high_confidence), 'stat__value--green'),
    statTile('ACTIVE', num(summary.active), 'stat__value--red'),
    statTile('AI VERIFIED', num(summary.ai_verified), 'stat__value--purple'),
    statTile('CAM HITS', num(summary.camera_detections), 'stat__value--orange'),
    statTile('AVG CONF', `${Math.round(summary.avg_confidence)}%`),
  ]);

  // Timeline: total bars with the high-confidence portion stacked inside.
  const peak = Math.max(1, timeline.peak);
  const chart = el('div.chart', {}, timeline.buckets.map((bucket) => {
    const height = (bucket.count / peak) * 100;
    const bar = el('div.chart__bar', {
      class: bucket.count === peak && peak > 0 ? 'chart__bar--hot' : '',
      style: { height: `${Math.max(1, height)}%` },
      title: `${shortTime(bucket.start)} — ${bucket.count} sightings (${bucket.high} high confidence)`,
    }, [
      bucket.high
        ? el('div.chart__bar-high', {
            style: { height: `${(bucket.high / Math.max(1, bucket.count)) * 100}%` },
          })
        : null,
    ]);
    return bar;
  }));

  const first = timeline.buckets[0];
  const last = timeline.buckets[timeline.buckets.length - 1];

  const children = [
    rangeBar,
    tiles,
    el('div.px-xs.dim', { text: 'ACTIVITY TIMELINE' }),
    chart,
    el('div.chart__axis', {}, [
      el('span', { text: first ? shortTime(first.start) : '' }),
      el('span', { text: `PEAK ${timeline.peak}` }),
      el('span', { text: last ? shortTime(last.end) : 'NOW' }),
    ]),
  ];

  if (full) {
    const areaPeak = Math.max(1, ...areas.map((a) => a.count));
    children.push(
      el('div.px-xs.dim', { style: { marginTop: 'var(--sp-6)' }, text: 'BY AREA' }),
      el('div.hbar', { style: { marginTop: 'var(--sp-3)' } }, areas.map((area) => {
        const track = el('div.bar__track', {}, [
          el('div.bar__fill', { style: { width: '0%' } }),
        ]);
        requestAnimationFrame(() => {
          track.firstChild.style.width = `${(area.count / areaPeak) * 100}%`;
        });
        return el('button.hbar__row', {
          type: 'button',
          onclick: () => window.dispatchEvent(
            new CustomEvent('spidey:focus-area', { detail: area.area }),
          ),
          title: `${area.area} — ${area.share}% of activity, avg ${area.avg_confidence}%`,
        }, [
          el('div.hbar__name', { text: area.area }),
          track,
          el('div.bar__value', { text: String(area.count) }),
        ]);
      })),

      el('div.px-xs.dim', { style: { marginTop: 'var(--sp-6)' }, text: 'CONFIDENCE DISTRIBUTION' }),
      el('div', { style: { marginTop: 'var(--sp-3)' } }, confidence.map((band) => {
        const toneClass = {
          green: 'bar__fill--green', cyan: '', orange: 'bar__fill--orange',
          white: 'bar__fill--white', muted: 'bar__fill--white',
        }[band.tone] || '';
        return barRow(band.band, band.share, toneClass);
      })),

      el('div.px-xs.dim', { style: { marginTop: 'var(--sp-6)' }, text: 'ACTIVITY BY HOUR (LOCAL)' }),
      (() => {
        const hourPeak = Math.max(1, hours.peak);
        const nowHour = new Date().getHours();
        return el('div.clock24', { style: { marginTop: 'var(--sp-3)' } },
          hours.hours.map((entry) => el('div.clock24__spoke', {
            class: entry.hour === nowHour ? 'clock24__spoke--now'
                 : entry.count === hours.peak && hours.peak > 0 ? 'clock24__spoke--peak' : '',
            style: { height: `${Math.max(4, (entry.count / hourPeak) * 100)}%` },
            title: `${String(entry.hour).padStart(2, '0')}:00 — ${entry.count} sightings`,
          })));
      })(),
      el('div.chart__axis', {}, [
        el('span', { text: '00' }), el('span', { text: '06' }),
        el('span', { text: '12' }), el('span', { text: '18' }), el('span', { text: '23' }),
      ]),

      el('div.px-xs.dim', { style: { marginTop: 'var(--sp-6)' }, text: 'BY SOURCE' }),
      el('div', { style: { marginTop: 'var(--sp-3)' } },
        sources.map((s) => barRow(SOURCE_LABEL[s.source] || s.source, s.share))),

      cameras && cameras.length
        ? el('div', {}, [
            el('div.px-xs.dim', { style: { marginTop: 'var(--sp-6)' }, text: 'TOP CAMERAS' }),
            el('div', { style: { marginTop: 'var(--sp-3)' } }, cameras.map((camera) =>
              kv(`${camera.camera_id} ${camera.label}`,
                 `${camera.count} · ${Math.round(camera.avg_confidence)}%`))),
          ])
        : null,
    );
  }

  mount(container, children);
}

// ---------------------------------------------------------------------------
// SYSTEM STATUS  (measured, never asserted)
// ---------------------------------------------------------------------------

export function renderSystem(container) {
  const status = state.systemStatus;
  if (!status) { mount(container, emptyState('QUERYING SUBSYSTEMS…')); return; }

  const streamOk = state.streamState === 'open';

  mount(container, [
    el('div', {
      style: { display: 'flex', alignItems: 'center', gap: 'var(--sp-3)',
               marginBottom: 'var(--sp-4)' },
    }, [
      el('span.dot', { class: status.online ? 'dot--pulse' : '',
                       style: { color: status.online ? 'var(--green)' : 'var(--red)' } }),
      el('span.px-sm', { class: status.online ? 't-green' : 't-red', text: status.state }),
      el('span.px-xs.dim', { style: { marginLeft: 'auto' },
                             text: `UP ${duration(status.uptime_sec / 60)}` }),
    ]),

    ...status.subsystems.map((sub) => {
      // The browser knows the true stream state; trust it over the server's view.
      const ok = sub.id === 'stream' ? streamOk : sub.ok;
      const label = sub.id === 'stream'
        ? (streamOk ? 'CONNECTED' : state.streamState === 'connecting' ? 'CONNECTING' : 'OFFLINE')
        : sub.state;
      const tone = sub.id === 'demo' && sub.is_demo ? 't-orange'
                 : ok ? 't-green' : 't-red';
      return el('div.kv', {}, [
        el('div.kv__k', {}, [
          el('span.dot', {
            style: { color: `var(--${sub.id === 'demo' && sub.is_demo ? 'orange' : ok ? 'green' : 'red'})`,
                     marginRight: '6px' },
          }),
          sub.label,
        ]),
        el('div.kv__v', { class: tone, title: sub.detail || '', text: label }),
      ]);
    }),

    el('div', { style: { marginTop: 'var(--sp-4)', display: 'flex', gap: 'var(--sp-2)', flexWrap: 'wrap' } }, [
      el('button.btn.btn--sm', {
        class: state.demoMode ? 'btn--active' : '',
        type: 'button',
        onclick: () => window.dispatchEvent(new CustomEvent('spidey:toggle-demo')),
      }, [`DEMO MODE ${state.demoMode ? 'ON' : 'OFF'}`]),
      el('button.btn.btn--sm', {
        class: state.prefs.sound ? 'btn--active' : '',
        type: 'button',
        onclick: () => window.dispatchEvent(new CustomEvent('spidey:toggle-sound')),
      }, [`SOUND ${state.prefs.sound ? 'ON' : 'OFF'}`]),
      el('button.btn.btn--sm', {
        class: state.prefs.reducedMotion ? 'btn--active' : '',
        type: 'button',
        onclick: () => window.dispatchEvent(new CustomEvent('spidey:toggle-motion')),
      }, [`MOTION ${state.prefs.reducedMotion ? 'REDUCED' : 'FULL'}`]),
      el('button.btn.btn--sm', {
        class: state.prefs.bootSequence ? 'btn--active' : '',
        type: 'button',
        onclick: () => window.dispatchEvent(new CustomEvent('spidey:toggle-boot')),
      }, [`BOOT SEQ ${state.prefs.bootSequence ? 'ON' : 'OFF'}`]),
    ]),

    // Purely decorative flavour readouts — labelled as such.
    el('div.px-xs.dim', { style: { marginTop: 'var(--sp-5)' }, text: 'FIELD SYSTEMS (FLAVOUR)' }),
    el('div.eggs', { style: { marginTop: 'var(--sp-3)' } }, easterEggs().map(([key, value, tone]) =>
      el('div.egg', {}, [
        el('span', { text: key }),
        el('b', { class: tone, text: value }),
      ]))),

    el('div.px-xs.dim', {
      style: { marginTop: 'var(--sp-5)', lineHeight: '1.9' },
      text: status.disclaimer,
    }),
  ]);
}

/**
 * Fictional status flavour. Derived from live state where it can be, so even
 * the jokes move — but explicitly grouped under a "FLAVOUR" heading so nobody
 * reads them as real telemetry.
 */
function easterEggs() {
  const list = visibleSightings();
  const active = list.filter((s) => s.status === 'active' || s.status === 'confirmed').length;
  const avg = list.length
    ? list.reduce((sum, s) => sum + s.confidence, 0) / list.length : 0;

  return [
    ['WEB FLUID', active > 6 ? 'LOW' : 'READY', active > 6 ? 't-orange' : 't-green'],
    ['SPIDER-SENSE', active ? 'TINGLING' : 'CALM', active ? 't-red' : 't-green'],
    ['WEB SHOOTERS', 'ONLINE', 't-green'],
    ['NED MODE', 'ENGAGED', 't-cyan'],
    ['STARK NETWORK', 'OFFLINE', 'dim'],
    ['QUEENS BRIDGE', avg > 80 ? 'ALERT' : 'CLEAR', avg > 80 ? 't-orange' : 't-green'],
    ['MULTIVERSE SIGNAL', avg > 90 ? 'RISING' : 'LOW', avg > 90 ? 't-purple' : 'dim'],
    ['AUNT MAY', 'DO NOT TELL', 't-orange'],
  ];
}

// ---------------------------------------------------------------------------
// GLOBAL NETWORK
// ---------------------------------------------------------------------------

export function renderTicker() {
  const track = document.getElementById('ticker-track');
  const sys = document.getElementById('footer-sys');
  if (!track) return;

  const network = state.network;
  if (!network || !network.cities.length) {
    mount(track, [el('span.ticker__item', {}, ['NETWORK DATA UNAVAILABLE'])]);
  } else {
    const build = () => network.cities.map((city) => el('button.ticker__item', {
      type: 'button',
      title: `${city.name} — ${city.total} total, ${city.last_24h} in 24h`,
      onclick: () => window.dispatchEvent(
        new CustomEvent('spidey:set-city', { detail: city.id }),
      ),
    }, [
      el('span.dot', {
        style: { color: city.id === state.cityId ? 'var(--cyan)' : 'var(--blue-mid)' },
      }),
      `${city.name} `,
      el('strong', { text: num(city.total) }),
      el('span.dim', { text: `+${city.last_24h}/24H` }),
    ]));
    // Duplicated so the CSS marquee can loop seamlessly at -50%.
    mount(track, [...build(), ...build()]);
  }

  if (sys) {
    const status = state.systemStatus;
    const streamOk = state.streamState === 'open';
    mount(sys, [
      el('span.sysline', { class: streamOk ? '' : 'sysline--err' }, [
        el('span.dot', { class: streamOk ? 'dot--pulse' : '' }),
        streamOk ? 'STREAM LIVE' : 'STREAM OFFLINE',
      ]),
      state.demoMode
        ? el('span.badge.badge--demo', {}, [el('span.dot.dot--pulse'), 'DEMO NETWORK'])
        : null,
      status
        ? el('span.sysline', { class: status.online ? '' : 'sysline--warn' }, [
            el('span.dot'), status.online ? 'ALL SYSTEMS' : 'DEGRADED',
          ])
        : null,
    ]);
  }
}

export function renderNetwork(container) {
  const network = state.network;
  if (!network) { mount(container, emptyState('LOADING NETWORK…')); return; }

  const peak = Math.max(1, ...network.cities.map((c) => c.total));

  mount(container, [
    el('div', {
      style: { display: 'grid', gap: 'var(--sp-2)', marginBottom: 'var(--sp-5)',
               gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))' },
    }, [
      statTile('GLOBAL TOTAL', num(network.grand_total)),
      statTile('LAST 24H', num(network.total_24h), 'stat__value--green'),
      statTile('CITIES', String(network.cities.length), 'stat__value--purple'),
    ]),
    el('div', {}, network.cities.map((city) => {
      const track = el('div.bar__track', { style: { margin: '0 var(--sp-4)' } }, [
        el('div.bar__fill', {
          class: city.id === state.cityId ? '' : 'bar__fill--white',
          style: { width: '0%' },
        }),
      ]);
      requestAnimationFrame(() => {
        track.firstChild.style.width = `${(city.total / peak) * 100}%`;
      });
      return el('button.netrow', {
        class: city.id === state.cityId ? 'is-active' : '',
        type: 'button',
        onclick: () => window.dispatchEvent(
          new CustomEvent('spidey:set-city', { detail: city.id }),
        ),
      }, [
        el('span.dot', {
          style: { color: city.id === state.cityId ? 'var(--cyan)' : 'var(--blue-mid)' },
        }),
        el('div', { style: { minWidth: 0 } }, [
          el('div.netrow__name', { text: city.name }),
          el('div.px-xs.dim', {
            text: city.last_sighting_age_sec !== null
              ? `LAST ${ago(city.last_sighting_age_sec)}` : 'NO ACTIVITY',
          }),
        ]),
        track,
        el('div', { style: { textAlign: 'right' } }, [
          el('div.netrow__total', { text: num(city.total) }),
          el('div.px-xs.dim', { text: `+${city.last_24h}` }),
        ]),
      ]);
    })),
  ]);
}
