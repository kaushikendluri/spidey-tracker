/**
 * SSE client + a tiny local event bus.
 *
 * EventSource reconnects on its own, but it cannot tell us the server died
 * mid-stream, so we also watch for silence: the server sends a keepalive every
 * ~15s, and if nothing arrives for 45s we treat the connection as stale and
 * force a reconnect. The connection state is reported honestly to the UI —
 * "NETWORK OFFLINE" appears because the stream really is down.
 */

const handlers = new Map();

export function on(type, fn) {
  if (!handlers.has(type)) handlers.set(type, new Set());
  handlers.get(type).add(fn);
  return () => handlers.get(type).delete(fn);
}

export function emit(type, payload) {
  const set = handlers.get(type);
  if (set) {
    for (const fn of set) {
      try { fn(payload); } catch (err) { console.error(`[events] ${type} handler failed`, err); }
    }
  }
  const wildcard = handlers.get('*');
  if (wildcard) {
    for (const fn of wildcard) {
      try { fn({ type, payload }); } catch (err) { console.error('[events] * handler failed', err); }
    }
  }
}

const SERVER_EVENTS = [
  'stream.connected',
  'sighting.created',
  'sighting.updated',
  'sighting.deleted',
  'prediction.updated',
  'camera.detected',
  'camera.status_changed',
  'alert.created',
  'network.updated',
  'system.status_changed',
];

const STALE_AFTER_MS = 45000;

let source = null;
let lastMessageAt = 0;
let staleTimer = null;
let reconnectTimer = null;
let attempt = 0;

function markAlive() {
  lastMessageAt = Date.now();
}

function checkStale() {
  if (!source) return;
  if (Date.now() - lastMessageAt > STALE_AFTER_MS) {
    console.warn('[stream] no traffic for 45s — reconnecting');
    emit('stream.state', 'closed');
    restart();
  }
}

function restart() {
  if (source) {
    source.close();
    source = null;
  }
  clearTimeout(reconnectTimer);
  // Exponential backoff, capped, so a downed server is not hammered.
  const delay = Math.min(15000, 800 * 2 ** Math.min(attempt, 4));
  attempt += 1;
  reconnectTimer = setTimeout(connect, delay);
}

export function connect() {
  if (source) return source;

  emit('stream.state', 'connecting');
  source = new EventSource('/api/stream');
  markAlive();

  source.onopen = () => {
    attempt = 0;
    markAlive();
    emit('stream.state', 'open');
  };

  source.onerror = () => {
    // EventSource fires this on transient blips too; only the closed state is
    // conclusive enough to report as offline.
    if (source && source.readyState === EventSource.CLOSED) {
      emit('stream.state', 'closed');
      restart();
    } else {
      emit('stream.state', 'connecting');
    }
  };

  // Unnamed messages (keepalives arrive as comments and never reach here).
  source.onmessage = (event) => {
    markAlive();
    dispatch(event);
  };

  for (const name of SERVER_EVENTS) {
    source.addEventListener(name, (event) => {
      markAlive();
      dispatch(event, name);
    });
  }

  clearInterval(staleTimer);
  staleTimer = setInterval(checkStale, 10000);

  return source;
}

function dispatch(event, name) {
  let payload;
  try {
    payload = JSON.parse(event.data);
  } catch (err) {
    console.warn('[stream] unparseable payload', event.data);
    return;
  }
  emit(payload.type || name || 'message', payload.data ?? {});
}

export function disconnect() {
  clearInterval(staleTimer);
  clearTimeout(reconnectTimer);
  if (source) { source.close(); source = null; }
  emit('stream.state', 'closed');
}

export function streamReady() {
  return Boolean(source && source.readyState === EventSource.OPEN);
}
