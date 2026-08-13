/**
 * Optional chiptune-style sound, synthesised with WebAudio.
 *
 * No audio files and no autoplay: the context is only created after the user
 * has interacted with the page, and stays muted until sound is switched on.
 * Volumes are deliberately low.
 */

let ctx = null;
let master = null;
let enabled = false;
let unlocked = false;

const VOICES = {
  ping:   { type: 'sine',     freq: 1180, to: 1560, dur: 0.09, gain: 0.05 },
  blip:   { type: 'square',   freq: 620,  to: 880,  dur: 0.05, gain: 0.03 },
  alert:  { type: 'sawtooth', freq: 340,  to: 180,  dur: 0.34, gain: 0.07 },
  new:    { type: 'square',   freq: 880,  to: 1320, dur: 0.13, gain: 0.05 },
  ned:    { type: 'triangle', freq: 520,  to: 700,  dur: 0.07, gain: 0.04 },
  click:  { type: 'square',   freq: 300,  to: 240,  dur: 0.03, gain: 0.02 },
  boot:   { type: 'triangle', freq: 220,  to: 660,  dur: 0.5,  gain: 0.05 },
  error:  { type: 'sawtooth', freq: 200,  to: 110,  dur: 0.22, gain: 0.06 },
};

function ensureContext() {
  if (ctx) return ctx;
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  if (!AudioCtx) return null;
  ctx = new AudioCtx();
  master = ctx.createGain();
  master.gain.value = 0.85;
  master.connect(ctx.destination);
  return ctx;
}

/** Called from the first real user gesture; browsers require this. */
export function unlock() {
  if (unlocked) return;
  unlocked = true;
  const context = ensureContext();
  if (context && context.state === 'suspended') context.resume().catch(() => {});
}

export function setEnabled(value) {
  enabled = Boolean(value);
  if (enabled) unlock();
  return enabled;
}

export function isEnabled() {
  return enabled;
}

export function play(name) {
  if (!enabled || !unlocked) return;
  const voice = VOICES[name];
  if (!voice) return;
  const context = ensureContext();
  if (!context || context.state !== 'running') return;

  const now = context.currentTime;
  const osc = context.createOscillator();
  const gain = context.createGain();

  osc.type = voice.type;
  osc.frequency.setValueAtTime(voice.freq, now);
  osc.frequency.exponentialRampToValueAtTime(Math.max(40, voice.to), now + voice.dur);

  // Short attack, exponential decay — reads as a console blip, not a tone.
  gain.gain.setValueAtTime(0.0001, now);
  gain.gain.exponentialRampToValueAtTime(voice.gain, now + 0.008);
  gain.gain.exponentialRampToValueAtTime(0.0001, now + voice.dur);

  osc.connect(gain).connect(master);
  osc.start(now);
  osc.stop(now + voice.dur + 0.02);
}

/** Boot chime: a short ascending arpeggio. */
export function playBootChord() {
  if (!enabled) return;
  [0, 90, 180, 300].forEach((delay, i) => {
    setTimeout(() => {
      const context = ensureContext();
      if (!context || context.state !== 'running') return;
      const now = context.currentTime;
      const osc = context.createOscillator();
      const gain = context.createGain();
      osc.type = 'square';
      osc.frequency.value = [392, 523, 659, 784][i];
      gain.gain.setValueAtTime(0.0001, now);
      gain.gain.exponentialRampToValueAtTime(0.035, now + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.22);
      osc.connect(gain).connect(master);
      osc.start(now);
      osc.stop(now + 0.24);
    }, delay);
  });
}
