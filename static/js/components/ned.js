/**
 * NED AI chat panel.
 *
 * The engine label is rendered from what the server reports, so the user can
 * always see whether they are talking to a language model or the deterministic
 * local intent engine. Actions returned with a reply are executed against the
 * dashboard, which is what lets NED drive the map and panels.
 */

import { state, set } from '../core/store.js';
import { api } from '../core/api.js';
import { el, mount, clear, esc, shortTime } from '../core/format.js';
import * as sound from '../core/sound.js';

const SUGGESTIONS = [
  'Where is he heading?',
  'Latest sighting',
  'Busiest area',
  'Show camera 7',
  "Today's stats",
  'Show the network',
  'Why is this high confidence?',
];

let sending = false;
let typingNode = null;

export function initNed() {
  const form = document.getElementById('ned-form');
  const input = document.getElementById('ned-input');
  const clearBtn = document.getElementById('ned-clear');
  const suggest = document.getElementById('ned-suggest');

  if (form) {
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const message = input.value.trim();
      if (!message) return;
      input.value = '';
      ask(message);
    });
  }

  if (clearBtn) {
    clearBtn.addEventListener('click', async () => {
      try {
        await api.aiClear();
        set({ aiMessages: [] });
        renderNed();
      } catch (err) {
        pushLocal('assistant', `Couldn't clear the log: ${err.message}`);
      }
    });
  }

  if (suggest) {
    mount(suggest, SUGGESTIONS.map((text) => el('button.btn.btn--sm.btn--ghost', {
      type: 'button',
      onclick: () => ask(text),
    }, [text.toUpperCase()])));
  }

  loadHistory();
  loadEngine();
}

async function loadEngine() {
  try {
    const status = await api.aiStatus();
    set({ aiEngine: status });
    renderEngineBadge();
  } catch (err) {
    set({ aiEngine: { label: 'UNAVAILABLE', is_real_model: false, ready: false } });
    renderEngineBadge();
  }
}

function renderEngineBadge() {
  const node = document.getElementById('ned-engine');
  if (!node) return;
  const engine = state.aiEngine;
  if (!engine) return;

  node.className = `badge badge--${engine.is_real_model ? 'green' : 'muted'}`;
  node.textContent = engine.label;
  node.title = engine.is_real_model
    ? 'Replies come from a language model with tool access to live app data.'
    : (engine.note || 'Deterministic intent matching over live app data — not a language model.');
}

async function loadHistory() {
  try {
    const data = await api.aiHistory();
    if (data.messages && data.messages.length) {
      set({ aiMessages: data.messages });
    } else {
      set({
        aiMessages: [{
          role: 'assistant',
          content: 'NED online. I can pull the latest sighting, project where the '
                 + 'subject is heading, rank the busiest areas, open a camera or '
                 + 'run the numbers. All data here is fictional or simulated.',
          ts: Date.now() / 1000,
        }],
      });
    }
  } catch (err) {
    set({
      aiMessages: [{
        role: 'assistant',
        content: `Couldn't reach the assistant service: ${err.message}`,
        ts: Date.now() / 1000,
      }],
    });
  }
  renderNed();
}

function pushLocal(role, content, extra = {}) {
  set({ aiMessages: [...state.aiMessages, { role, content, ts: Date.now() / 1000, ...extra }] });
  renderNed();
}

export async function ask(message) {
  if (sending) return;
  sending = true;

  pushLocal('user', message);
  sound.play('blip');
  showTyping();

  try {
    const result = await api.aiChat(message, {
      city_id: state.cityId,
      selected_sighting_id: state.selectedSightingId,
      map_mode: state.mapMode,
      visible_count: state.sightings.length,
    });

    hideTyping();
    pushLocal('assistant', result.reply, {
      engine: result.engine,
      engine_label: result.engine_label,
      actions: result.actions || [],
      data: result.data || {},
      degraded: result.degraded,
    });
    sound.play('ned');

    // Reflect a degraded engine in the badge immediately.
    if (result.engine_label && state.aiEngine
        && result.engine_label !== state.aiEngine.label) {
      set({ aiEngine: { ...state.aiEngine, label: result.engine_label } });
      renderEngineBadge();
    }

    for (const action of result.actions || []) {
      window.dispatchEvent(new CustomEvent('spidey:action', { detail: action }));
    }
  } catch (err) {
    hideTyping();
    pushLocal('assistant', `I couldn't complete that: ${err.message}`);
    sound.play('error');
  } finally {
    sending = false;
  }
}

function showTyping() {
  const log = document.getElementById('ned-log');
  if (!log) return;
  typingNode = el('div.ned__msg.ned__msg--ned', {}, [
    el('div.ned__who', { text: 'NED' }),
    el('div.ned__typing', {}, [el('span'), el('span'), el('span')]),
  ]);
  log.appendChild(typingNode);
  log.scrollTop = log.scrollHeight;
}

function hideTyping() {
  if (typingNode && typingNode.parentNode) typingNode.parentNode.removeChild(typingNode);
  typingNode = null;
}

/** Short action label so the buttons under a reply say what they will do. */
function actionLabel(action) {
  switch (action.type) {
    case 'map.focus':     return 'GO TO LOCATION';
    case 'sighting.open': return 'OPEN SIGHTING';
    case 'camera.open':   return `OPEN ${action.camera_id || 'CAMERA'}`;
    case 'panel.open':    return `OPEN ${(action.panel || '').toUpperCase()}`;
    case 'map.mode':      return `${(action.mode || '').toUpperCase()} MODE`;
    case 'city.set':      return `SWITCH TO ${(action.city_id || '').toUpperCase()}`;
    case 'filter.set':    return 'APPLY FILTER';
    default:              return action.type.toUpperCase();
  }
}

export function renderNed() {
  const log = document.getElementById('ned-log');
  if (!log) return;

  const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 60;

  mount(log, state.aiMessages.map((message) => {
    const isUser = message.role === 'user';
    return el('div.ned__msg', { class: isUser ? 'ned__msg--user' : 'ned__msg--ned' }, [
      el('div.ned__who', {
        text: isUser ? 'YOU' : `NED${message.engine_label ? ` · ${message.engine_label}` : ''}`,
      }),
      el('div.ned__bubble', { text: message.content }),
      message.degraded
        ? el('div.px-xs', { class: 't-orange', text: 'DEGRADED — FELL BACK TO LOCAL ENGINE' })
        : null,
      message.actions && message.actions.length
        ? el('div.ned__actions', {}, message.actions.map((action) =>
            el('button.btn.btn--sm.btn--ghost', {
              type: 'button',
              onclick: () => window.dispatchEvent(
                new CustomEvent('spidey:action', { detail: action }),
              ),
            }, [actionLabel(action)])))
        : null,
    ]);
  }));

  if (atBottom) log.scrollTop = log.scrollHeight;
}
