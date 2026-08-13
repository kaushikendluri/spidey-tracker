/**
 * Detail views rendered into the overlay: sighting dossier, camera detail and
 * the report form.
 */

import { state, set } from '../core/store.js';
import { api, ApiError } from '../core/api.js';
import {
  el, mount, clear, esc, ago, agoFromTs, shortTime, dateTime, num, pct, km, kmh,
  confidenceTone, TONE_CLASS, BAR_CLASS, SOURCE_LABEL, STATUS_LABEL, CAMERA_TONE,
  barRow, kv, statTile, emptyState, demoBadge,
} from '../core/format.js';
import * as sound from '../core/sound.js';

// ---------------------------------------------------------------------------
// SIGHTING DOSSIER
// ---------------------------------------------------------------------------

export function renderDossier(container, sighting) {
  if (!sighting) { mount(container, emptyState('NO SIGHTING SELECTED')); return; }

  const tone = confidenceTone(sighting.confidence, sighting.status);
  const analysis = sighting.analysis;

  mount(container, el('div.dossier', {}, [

    el('div.dossier__hero', {}, [
      el('div', {}, [
        el('div.dossier__ref', { text: `SPIDEY SIGHTING ${sighting.ref}` }),
        el('div.px-sm', { style: { color: 'var(--text)', marginTop: 'var(--sp-2)' },
                          text: sighting.area }),
        el('div.px-xs.dim', { style: { marginTop: 'var(--sp-2)' },
                              text: `${sighting.city_name} · ${dateTime(sighting.ts)}` }),
      ]),
      el('div.dossier__conf', {}, [
        el('div.px-xs.dim', { text: 'CONFIDENCE' }),
        el('div.dossier__conf-value', { class: TONE_CLASS[tone],
                                        text: `${Math.round(sighting.confidence)}%` }),
        el('div', { style: { display: 'flex', gap: 'var(--sp-2)', justifyContent: 'flex-end',
                             marginTop: 'var(--sp-2)' } }, [
          el('span.badge', { class: `badge--${tone === 'confirmed' ? 'green' : tone === 'active' ? 'red' : tone === 'medium' ? 'orange' : 'muted'}` },
            [STATUS_LABEL[sighting.status] || sighting.status.toUpperCase()]),
          sighting.is_demo ? demoBadge() : null,
        ]),
      ]),
    ]),

    // --- media
    sighting.image_url
      ? el('img.dossier__media', { src: sighting.image_url,
                                   alt: `Photograph submitted with sighting ${sighting.ref}` })
      : null,
    sighting.video_url
      ? el('video.dossier__media', { src: sighting.video_url, controls: true, preload: 'metadata' })
      : null,

    // --- core facts
    el('div.dossier__grid', {}, [
      statTile('LOCATION', sighting.area),
      statTile('TIME', shortTime(sighting.ts), '', agoFromTs(sighting.ts)),
      statTile('DIRECTION', sighting.direction_label || '—', '',
               sighting.direction !== null ? `${Math.round(sighting.direction)}°` : ''),
      statTile('EST SPEED', sighting.speed_kmh ? kmh(sighting.speed_kmh) : '—'),
      statTile('SOURCE', sighting.camera_id || SOURCE_LABEL[sighting.source] || sighting.source),
      statTile('STATUS', STATUS_LABEL[sighting.status] || sighting.status,
               tone === 'confirmed' ? 'stat__value--green' : tone === 'active' ? 'stat__value--red' : ''),
    ]),

    // --- AI analysis
    el('div', {}, [
      el('div', { style: { display: 'flex', alignItems: 'center', gap: 'var(--sp-3)',
                           marginTop: 'var(--sp-5)', marginBottom: 'var(--sp-3)' } }, [
        el('div.px-sm.t-cyan', { text: 'AI ANALYSIS' }),
        analysis
          ? el('span.badge', {
              class: analysis.is_real_model ? 'badge--green' : 'badge--demo',
              title: analysis.is_real_model
                ? 'Output of a real detection model.'
                : 'Not a trained detector — heuristic or simulated scoring.',
            }, [analysis.label])
          : el('span.badge.badge--muted', {}, ['NOT ANALYSED']),
      ]),

      analysis
        ? el('div', {}, [
            el('div', { style: { display: 'flex', alignItems: 'baseline',
                                 gap: 'var(--sp-4)', marginBottom: 'var(--sp-4)' } }, [
              el('div.px-xs.dim', { text: 'SPIDER-MAN PROBABILITY' }),
              el('div.term-xl', { class: TONE_CLASS[tone],
                                  style: { marginLeft: 'auto' },
                                  text: `${Math.round(analysis.probability)}%` }),
            ]),
            barRow('VISUAL MATCH', analysis.visual_match, BAR_CLASS[tone]),
            barRow('MOTION MATCH', analysis.motion_match, BAR_CLASS[tone]),
            barRow('PATTERN MATCH', analysis.pattern_match, BAR_CLASS[tone]),
            barRow('LOCATION MATCH', analysis.location_match, BAR_CLASS[tone]),
            el('div.dossier__notes', { style: { marginTop: 'var(--sp-4)' },
                                       text: analysis.notes || '' }),
          ])
        : emptyState('NO ANALYSIS RECORD FOR THIS SIGHTING'),
    ]),

    // --- report detail
    sighting.description
      ? el('div', {}, [
          el('div.px-sm.t-cyan', { style: { marginTop: 'var(--sp-5)', marginBottom: 'var(--sp-3)' },
                                   text: 'REPORT' }),
          el('div.dossier__notes', { text: sighting.description }),
          sighting.reporter
            ? el('div.px-xs.dim', { style: { marginTop: 'var(--sp-2)' },
                                    text: `REPORTED BY ${sighting.reporter}` })
            : null,
        ])
      : null,

    // --- movement context
    sighting.context && sighting.context.length
      ? el('div', {}, [
          el('div.px-sm.t-cyan', { style: { marginTop: 'var(--sp-5)', marginBottom: 'var(--sp-3)' },
                                   text: 'NEARBY IN TIME' }),
          ...sighting.context.map((c) => el('button.kv', {
            type: 'button',
            style: { width: '100%', background: 'none', border: 0, borderBottom: '1px dashed rgba(10,74,110,.5)',
                     cursor: 'pointer', textAlign: 'left', fontFamily: 'inherit', color: 'inherit' },
            onclick: () => window.dispatchEvent(new CustomEvent('spidey:select', { detail: c.id })),
          }, [
            el('div.kv__k', { text: `${c.ref} · ${c.area}` }),
            el('div.kv__v', { text: `${Math.round(c.confidence)}% · ${agoFromTs(c.ts)}` }),
          ])),
        ])
      : null,

    // --- operator actions
    el('div', { style: { display: 'flex', gap: 'var(--sp-2)', flexWrap: 'wrap',
                         marginTop: 'var(--sp-6)', paddingTop: 'var(--sp-4)',
                         borderTop: '1px solid var(--blue-line)' } }, [
      el('button.btn.btn--sm', {
        type: 'button',
        onclick: () => window.dispatchEvent(new CustomEvent('spidey:focus-point', {
          detail: { lat: sighting.latitude, lon: sighting.longitude, zoom: 16 },
        })),
      }, ['CENTRE ON MAP']),
      el('button.btn.btn--sm.btn--success', {
        type: 'button',
        disabled: sighting.status === 'confirmed',
        onclick: () => updateStatus(sighting.id, 'confirmed'),
      }, ['CONFIRM']),
      el('button.btn.btn--sm', {
        type: 'button',
        disabled: sighting.status === 'dismissed',
        onclick: () => updateStatus(sighting.id, 'dismissed'),
      }, ['DISMISS']),
      el('button.btn.btn--sm.btn--purple', {
        type: 'button',
        onclick: () => reanalyze(sighting.id),
      }, ['RE-ANALYSE']),
      el('button.btn.btn--sm.btn--danger', {
        type: 'button',
        style: { marginLeft: 'auto' },
        onclick: () => removeSighting(sighting.id),
      }, ['DELETE']),
    ]),

    el('div.px-xs.dim', { style: { marginTop: 'var(--sp-4)', lineHeight: '1.9' },
                          text: 'Fictional record. No real person, camera or location is described.' }),
  ]));
}

async function updateStatus(id, status) {
  try {
    const updated = await api.updateSighting(id, { status });
    sound.play('blip');
    window.dispatchEvent(new CustomEvent('spidey:sighting-updated', { detail: updated }));
  } catch (err) {
    window.dispatchEvent(new CustomEvent('spidey:toast', {
      detail: { title: 'UPDATE FAILED', body: err.message, severity: 'warning' },
    }));
  }
}

async function reanalyze(id) {
  try {
    const updated = await api.reanalyze(id);
    sound.play('ping');
    window.dispatchEvent(new CustomEvent('spidey:sighting-updated', { detail: updated }));
    window.dispatchEvent(new CustomEvent('spidey:toast', {
      detail: {
        title: 'RE-ANALYSIS COMPLETE',
        body: `${updated.ref} now ${Math.round(updated.confidence)}%`,
        severity: 'info',
      },
    }));
  } catch (err) {
    window.dispatchEvent(new CustomEvent('spidey:toast', {
      detail: { title: 'RE-ANALYSIS FAILED', body: err.message, severity: 'warning' },
    }));
  }
}

async function removeSighting(id) {
  try {
    await api.deleteSighting(id);
    sound.play('error');
    window.dispatchEvent(new CustomEvent('spidey:close-overlay'));
  } catch (err) {
    window.dispatchEvent(new CustomEvent('spidey:toast', {
      detail: { title: 'DELETE FAILED', body: err.message, severity: 'warning' },
    }));
  }
}

// ---------------------------------------------------------------------------
// CAMERA DETAIL
// ---------------------------------------------------------------------------

export function renderCameraDetail(container, camera) {
  if (!camera) { mount(container, emptyState('CAMERA NOT FOUND')); return; }

  const tone = CAMERA_TONE[camera.status] || 'muted';

  mount(container, [
    el('div', { style: { display: 'flex', alignItems: 'flex-start', gap: 'var(--sp-5)',
                         marginBottom: 'var(--sp-5)' } }, [
      el('div', {}, [
        el('div.dossier__ref', { text: camera.id }),
        el('div.px-sm', { style: { color: 'var(--text)', marginTop: 'var(--sp-2)' },
                          text: camera.label }),
        el('div.px-xs.dim', { style: { marginTop: 'var(--sp-2)' },
                              text: `${camera.location_name || ''} · ${camera.city_name}` }),
      ]),
      el('div', { style: { marginLeft: 'auto', textAlign: 'right' } }, [
        el('span.badge', { class: `badge--${tone}` }, [
          el('span.dot', { class: camera.status === 'live' ? 'dot--pulse' : '' }),
          camera.status.toUpperCase(),
        ]),
        el('div.px-xs.dim', { style: { marginTop: 'var(--sp-2)' },
                              text: `UPDATED ${ago(camera.status_age_sec)}` }),
      ]),
    ]),

    // Simulated viewport — deliberately looks like a feed, labelled as not one.
    el('div.cam__view', { style: { aspectRatio: '16 / 9', marginBottom: 'var(--sp-4)' } }, [
      el('div.cam__noise'),
      el('div.cam__sweep'),
      el('div.cam__id', { text: camera.id }),
      camera.status === 'detected' ? el('div.cam__reticle') : null,
      el('div.cam__mock', { text: 'SIMULATED FEED — NOT A REAL CAMERA' }),
    ]),

    el('div', { style: { display: 'grid', gap: 'var(--sp-2)', marginBottom: 'var(--sp-5)',
                         gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr))' } }, [
      statTile('HEADING', `${Math.round(camera.heading)}°`),
      statTile('DETECTIONS', String((camera.detections || []).length)),
      statTile('LAST HIT', camera.last_detection
        ? `${Math.round(camera.last_detection.confidence)}%` : '—',
        camera.last_detection && camera.last_detection.confidence >= 85 ? 'stat__value--green' : ''),
      statTile('FEED', camera.is_mock ? 'MOCK' : 'LIVE', 'stat__value--orange'),
    ]),

    el('div', { style: { display: 'flex', gap: 'var(--sp-2)', flexWrap: 'wrap',
                         marginBottom: 'var(--sp-5)' } },
      ['live', 'analyzing', 'offline', 'error'].map((status) =>
        el('button.btn.btn--sm', {
          class: camera.status === status ? 'btn--active' : '',
          type: 'button',
          onclick: async () => {
            try {
              await api.setCameraStatus(camera.id, status);
              sound.play('click');
            } catch (err) {
              window.dispatchEvent(new CustomEvent('spidey:toast', {
                detail: { title: 'CAMERA UPDATE FAILED', body: err.message, severity: 'warning' },
              }));
            }
          },
        }, [`SET ${status.toUpperCase()}`]))),

    el('div.px-sm.t-cyan', { style: { marginBottom: 'var(--sp-3)' }, text: 'DETECTION LOG' }),
    (camera.detections && camera.detections.length)
      ? el('div', {}, camera.detections.map((d) => el('button.kv', {
          type: 'button',
          style: { width: '100%', background: 'none', border: 0,
                   borderBottom: '1px dashed rgba(10,74,110,.5)',
                   cursor: d.sighting_id ? 'pointer' : 'default',
                   textAlign: 'left', fontFamily: 'inherit', color: 'inherit' },
          onclick: () => {
            if (d.sighting_id) {
              window.dispatchEvent(new CustomEvent('spidey:select', { detail: d.sighting_id }));
            }
          },
        }, [
          el('div.kv__k', {}, [
            `${shortTime(d.ts)} · ${d.ref || 'UNLINKED'}`,
            d.is_demo ? el('span.badge.badge--demo', { style: { marginLeft: '6px', fontSize: '5px' } }, ['DEMO']) : null,
          ]),
          el('div.kv__v', { text: `${Math.round(d.confidence)}%` }),
        ])))
      : emptyState('NO DETECTIONS LOGGED'),

    el('div.px-xs.dim', { style: { marginTop: 'var(--sp-5)', lineHeight: '1.9' },
                          text: camera.feed_notice || '' }),
  ]);
}

// ---------------------------------------------------------------------------
// REPORT FORM
// ---------------------------------------------------------------------------

const STEPS = [
  ['upload', 'UPLOADING'],
  ['image', 'IMAGE RECEIVED'],
  ['location', 'LOCATION VERIFIED'],
  ['analysis', 'AI ANALYSIS'],
  ['confidence', 'CONFIDENCE CALCULATED'],
  ['registered', 'SIGHTING REGISTERED'],
];

export function renderReportForm(container) {
  const city = state.cities.find((c) => c.id === state.cityId);
  const locations = state.locations.filter((l) => l.city_id === state.cityId);

  const stepsNode = el('div.steps', { hidden: true });
  const errorNode = el('div.px-xs', { class: 't-red', style: { minHeight: '14px' } });
  let imageFile = null;
  let videoFile = null;
  let pickedLat = city ? city.latitude : null;
  let pickedLon = city ? city.longitude : null;

  const coordNode = el('div.px-xs.dim', {
    text: pickedLat !== null ? `${pickedLat.toFixed(5)}, ${pickedLon.toFixed(5)}` : 'NO POSITION',
  });

  const preview = el('div.drop__preview-wrap');

  const drop = el('label.drop', {}, [
    el('input', {
      type: 'file', accept: 'image/*', 'aria-label': 'Sighting photograph',
      onchange: (event) => {
        const file = event.target.files[0];
        if (!file) return;
        imageFile = file;
        const url = URL.createObjectURL(file);
        mount(preview, el('img.drop__preview', { src: url, alt: 'Selected photograph',
                                                 onload: () => URL.revokeObjectURL(url) }));
      },
    }),
    el('div.px-xs', { style: { color: 'var(--cyan)' }, text: 'DROP IMAGE OR CLICK TO SELECT' }),
    el('div.px-xs.dim', { style: { marginTop: 'var(--sp-3)' },
                          text: 'PNG JPG GIF WEBP — ANALYSED FOR SUIT PALETTE, CONTRAST AND EDGES' }),
    preview,
  ]);

  const locationSelect = el('select.select', { id: 'report-location' }, [
    el('option', { value: '', text: 'AUTO — NEAREST DISTRICT' }),
    ...locations.map((l) => el('option', { value: l.id, text: l.name })),
  ]);

  locationSelect.addEventListener('change', () => {
    const chosen = locations.find((l) => l.id === locationSelect.value);
    if (chosen) {
      pickedLat = chosen.latitude;
      pickedLon = chosen.longitude;
      coordNode.textContent = `${pickedLat.toFixed(5)}, ${pickedLon.toFixed(5)}`;
    }
  });

  const citySelect = el('select.select', {},
    state.cities.map((c) => el('option', {
      value: c.id, text: c.name, selected: c.id === state.cityId,
    })));

  const descField = el('textarea.textarea', {
    placeholder: 'What did you see? Direction of travel, altitude, anything distinctive.',
    maxlength: '1000',
  });

  const directionSelect = el('select.select', {}, [
    el('option', { value: '', text: 'UNKNOWN' }),
    ...['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'].map((label, i) =>
      el('option', { value: String(i * 45), text: label })),
  ]);

  const speedInput = el('input.input', { type: 'number', min: '0', max: '200',
                                         placeholder: 'KM/H (OPTIONAL)' });
  const reporterInput = el('input.input', { maxlength: '80', placeholder: 'YOUR CALLSIGN (OPTIONAL)' });

  const videoInput = el('input', {
    type: 'file', accept: 'video/*', class: 'input',
    onchange: (event) => { videoFile = event.target.files[0] || null; },
  });

  const submitBtn = el('button.btn.btn--lg.btn--success', { type: 'submit' }, ['SUBMIT REPORT']);

  const form = el('form', {
    onsubmit: async (event) => {
      event.preventDefault();
      errorNode.textContent = '';
      submitBtn.disabled = true;
      stepsNode.hidden = false;
      renderSteps(stepsNode, 'upload');

      try {
        if (pickedLat === null || pickedLon === null) {
          throw new ApiError('Pick a location on the map or choose a district.', 400, 'latitude');
        }

        const payload = new FormData();
        payload.set('city_id', citySelect.value);
        payload.set('latitude', String(pickedLat));
        payload.set('longitude', String(pickedLon));
        if (locationSelect.value) payload.set('location_id', locationSelect.value);
        if (descField.value.trim()) payload.set('description', descField.value.trim());
        if (directionSelect.value) payload.set('direction', directionSelect.value);
        if (speedInput.value) payload.set('speed_kmh', speedInput.value);
        if (reporterInput.value.trim()) payload.set('reporter', reporterInput.value.trim());
        payload.set('source', 'citizen');
        if (imageFile) payload.set('image', imageFile);
        if (videoFile) payload.set('video', videoFile);

        renderSteps(stepsNode, imageFile ? 'image' : 'location');
        await new Promise((r) => setTimeout(r, 260));
        renderSteps(stepsNode, 'location');
        await new Promise((r) => setTimeout(r, 220));
        renderSteps(stepsNode, 'analysis');

        const record = await api.createSighting(payload);

        renderSteps(stepsNode, 'confidence');
        await new Promise((r) => setTimeout(r, 200));
        renderSteps(stepsNode, 'registered', true);
        sound.play('new');

        window.dispatchEvent(new CustomEvent('spidey:toast', {
          detail: {
            title: 'SIGHTING REGISTERED',
            body: `${record.ref} — ${record.area} — ${Math.round(record.confidence)}%`,
            severity: 'info',
            sightingId: record.id,
          },
        }));

        setTimeout(() => {
          window.dispatchEvent(new CustomEvent('spidey:select', { detail: record.id }));
        }, 700);
      } catch (err) {
        renderSteps(stepsNode, null, false, true);
        errorNode.textContent = err.message;
        sound.play('error');
        submitBtn.disabled = false;
      }
    },
  }, [
    el('div', { style: { display: 'grid', gap: 'var(--sp-5)',
                         gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))' } }, [
      el('div.field', {}, [el('div.field__label', { text: 'CITY' }), citySelect]),
      el('div.field', {}, [el('div.field__label', { text: 'AREA' }), locationSelect]),
      el('div.field', {}, [el('div.field__label', { text: 'DIRECTION OF TRAVEL' }), directionSelect]),
      el('div.field', {}, [el('div.field__label', { text: 'ESTIMATED SPEED' }), speedInput]),
    ]),

    el('div.field', { style: { marginTop: 'var(--sp-5)' } }, [
      el('div.field__label', { text: 'POSITION' }),
      el('div', { style: { display: 'flex', alignItems: 'center', gap: 'var(--sp-4)' } }, [
        coordNode,
        el('button.btn.btn--sm', {
          type: 'button',
          onclick: () => {
            window.dispatchEvent(new CustomEvent('spidey:pick-position', {
              detail: (lat, lon) => {
                pickedLat = lat; pickedLon = lon;
                coordNode.textContent = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
              },
            }));
          },
        }, ['PICK ON MAP']),
        el('button.btn.btn--sm', {
          type: 'button',
          onclick: () => {
            if (!navigator.geolocation) {
              errorNode.textContent = 'Geolocation is not available in this browser.';
              return;
            }
            navigator.geolocation.getCurrentPosition(
              (pos) => {
                pickedLat = pos.coords.latitude;
                pickedLon = pos.coords.longitude;
                coordNode.textContent = `${pickedLat.toFixed(5)}, ${pickedLon.toFixed(5)}`;
              },
              (geoErr) => { errorNode.textContent = `Location denied: ${geoErr.message}`; },
            );
          },
        }, ['USE MY LOCATION']),
      ]),
    ]),

    el('div.field', { style: { marginTop: 'var(--sp-5)' } }, [
      el('div.field__label', { text: 'PHOTOGRAPH' }), drop,
    ]),

    el('div.field', { style: { marginTop: 'var(--sp-5)' } }, [
      el('div.field__label', { text: 'VIDEO (OPTIONAL)' }), videoInput,
    ]),

    el('div.field', { style: { marginTop: 'var(--sp-5)' } }, [
      el('div.field__label', { text: 'DESCRIPTION' }), descField,
    ]),

    el('div.field', { style: { marginTop: 'var(--sp-5)' } }, [
      el('div.field__label', { text: 'REPORTER' }), reporterInput,
    ]),

    el('div', { style: { marginTop: 'var(--sp-6)', display: 'flex', alignItems: 'center',
                         gap: 'var(--sp-5)', flexWrap: 'wrap' } }, [
      submitBtn, errorNode,
    ]),

    stepsNode,

    el('div.px-xs.dim', { style: { marginTop: 'var(--sp-6)', lineHeight: '1.9' },
      text: 'Submitted reports are scored by heuristic image measurement and geo '
          + 'correlation — not by a trained detector. Nothing here is transmitted '
          + 'to any external service.' }),
  ]);

  mount(container, form);
}

function renderSteps(container, current, done = false, failed = false) {
  const currentIndex = STEPS.findIndex(([key]) => key === current);
  mount(container, STEPS.map(([key, label], index) => {
    let cls = '';
    if (failed && index === Math.max(0, currentIndex)) cls = 'is-fail';
    else if (done || (currentIndex >= 0 && index < currentIndex)) cls = 'is-done';
    else if (index === currentIndex) cls = 'is-active';
    return el('div.step', { class: cls }, [
      el('div.step__mark', { text: cls === 'is-done' ? '✓' : cls === 'is-fail' ? '×' : '' }),
      el('span', { text: label }),
    ]);
  }));
}
